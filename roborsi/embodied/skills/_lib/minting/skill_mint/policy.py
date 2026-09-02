"""minting.skill_mint — promote a DataStore label into a catalogued task."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from roborsi.data.store import DataStore
from roborsi.embodied.skills import SHIPPED_ROOT


_SKILL_TEMPLATE = """\
---
name: {name}
kind: task
domain: {domain}
version: 0.1.0
description: {description}
metadata:
  tags: {tags}
  backends: {backends}
  minted_from_label: {source_label}
  minted_at: "{minted_at}"
  success_stats:
    total: {total}
    successes: {successes}
    success_rate: {success_rate:.3f}
  sim_tasks_seen: {sim_tasks}
---

# {name}

*Auto-minted from DataStore label `{source_label}` on {minted_at}.*

## Overview

Auto-generated task definition. {successes}/{total} episodes succeeded
before this skill was promoted into the catalogue. Backends observed:
{backends_str}. Sim tasks referenced in the source rollouts:
{sim_tasks_str}.

## What you should fill in

A minted task is a draft — pass the human-written bits in follow-up:

- Replace this overview with the real scene description.
- Add `objects`, `success_predicate`, `vlm_prompts` keys to the
  frontmatter so Planner and Judge can use them.
- Verify `bundle.yaml`'s parameters match your intent.

## Bundle

See `bundle.yaml` for the default pipeline
(`expert_replay` → `lerobot_build` → `pi0_finetune` → `success_rate`).
"""


_BUNDLE_TEMPLATE = {
    "task": None,      # filled at runtime
    "pipeline": [
        {
            "stage": "collect_expert",
            "skill": "collection.expert_replay",
            "farm": {"workers": 4, "episodes_per_worker": 13},
            "params": {
                "task": None,                # filled at runtime
                "backend": "robotwin",
                "seed_start": 0,
                "skill_label": None,
            },
        },
        {
            "stage": "build_dataset",
            "skill": "dataset.lerobot_build",
            "depends_on": ["collect_expert"],
            "params": {
                "skill_label": None,
                "dataset_name": None,
                "fps": 30,
                "robot_type": "aloha-agilex",
            },
        },
        {
            "stage": "train",
            "skill": "training.pi0_finetune",
            "depends_on": ["build_dataset"],
            "params": {
                "dataset": None,
                "base_model": "pi0.5",
                "steps": 20000,
                "batch_size": 8,
                "lr": 2.5e-5,
            },
        },
        {
            "stage": "evaluate_expert",
            "skill": "evaluation.success_rate",
            "params": {
                "task": None,
                "executor": "expert",
                "seeds": 20,
                "seed_start": 1000,
            },
        },
    ],
}


def run(
    source_label: str,
    new_task_name: str,
    domain: str = "manipulation",
    description: str | None = None,
    min_successes: int = 20,
    backends: list[str] | None = None,
    overwrite: bool = False,
    **_: Any,
) -> dict[str, Any]:
    if not source_label or not new_task_name:
        raise ValueError("skill_mint requires source_label and new_task_name")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", new_task_name):
        raise ValueError(
            f"new_task_name '{new_task_name}' must be snake_case ASCII "
            f"([a-z][a-z0-9_]*)"
        )
    description = description or f"Auto-minted task from label '{source_label}'."
    episodes = list(DataStore().list(source_label))
    successes = sum(1 for e in episodes if e.success)
    if successes < min_successes:
        raise RuntimeError(
            f"only {successes} successful episodes under label '{source_label}', "
            f"need {min_successes}; collect more before minting."
        )

    backends = backends or _infer_backends(episodes)
    target_dir = SHIPPED_ROOT / domain / new_task_name
    if target_dir.exists() and not overwrite:
        raise FileExistsError(f"{target_dir} already exists (use overwrite=true to replace)")
    if target_dir.exists():
        import shutil
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    sim_tasks = Counter(e.task for e in episodes if e.task)
    minted_at = datetime.now().isoformat(timespec="seconds")

    skill_md = _SKILL_TEMPLATE.format(
        name=new_task_name,
        domain=domain,
        description=description,
        tags=json.dumps(sorted({"sim", *(list(sim_tasks) if sim_tasks else [])})),
        backends=json.dumps(backends),
        source_label=source_label,
        minted_at=minted_at,
        total=len(episodes),
        successes=successes,
        success_rate=successes / len(episodes) if episodes else 0.0,
        sim_tasks=json.dumps(sorted(sim_tasks)),
        backends_str=", ".join(backends) or "(none)",
        sim_tasks_str=", ".join(sorted(sim_tasks)) or "(none)",
    )
    (target_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    bundle = _customise_bundle(new_task_name, source_label, backends[0] if backends else "robotwin")
    (target_dir / "bundle.yaml").write_text(
        yaml.safe_dump(bundle, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "skill": "skill_mint",
        "new_task": new_task_name,
        "path": str(target_dir),
        "episodes_total": len(episodes),
        "episodes_success": successes,
        "sim_tasks_seen": dict(sim_tasks),
        "backends": backends,
    }


def _infer_backends(episodes) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in episodes:
        meta = _load_meta(e.dir)
        be = str(meta.get("backend", "")) if isinstance(meta, dict) else ""
        if be and be not in seen:
            seen.add(be)
            out.append(be)
    return out or ["robotwin"]


def _load_meta(ep_dir: Path) -> dict[str, Any]:
    meta_path = ep_dir / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _customise_bundle(task_name: str, source_label: str, backend: str) -> dict[str, Any]:
    bundle = json.loads(json.dumps(_BUNDLE_TEMPLATE))   # deep copy
    bundle["task"] = task_name
    for stage in bundle["pipeline"]:
        p = stage.get("params", {})
        for key in ("task",):
            if key in p and p[key] is None:
                p[key] = task_name
        if "skill_label" in p and p["skill_label"] is None:
            p["skill_label"] = source_label
        if "dataset_name" in p and p["dataset_name"] is None:
            p["dataset_name"] = f"{task_name}_v1"
        if "dataset" in p and p["dataset"] is None:
            p["dataset"] = f"{task_name}_v1"
        if "backend" in p and backend:
            p["backend"] = backend
    return bundle
