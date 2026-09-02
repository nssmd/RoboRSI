"""Task bundle runner.

A task is a SKILL.md with ``kind: task`` plus a sibling ``bundle.yaml``
declaring which lifecycle skills apply with what params. This module
turns that bundle into an executable pipeline.

Light on features on purpose: sequential execution, honour ``enabled:
false``, respect ``depends_on`` only in the trivial "refuse to run a
stage if any dep failed" sense. True DAG scheduling / parallel stages
land later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from roborsi.embodied.skills import Skill, discover, run as run_skill


@dataclass
class Stage:
    stage: str
    skill: str                    # lifecycle-qualified or short name
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    enabled: bool = True
    farm: dict[str, Any] = field(default_factory=dict)   # {workers, episodes_per_worker, seed_start}


@dataclass
class Bundle:
    task: str
    source: Path
    stages: list[Stage]

    def stage(self, name: str) -> Stage | None:
        for s in self.stages:
            if s.stage == name:
                return s
        return None


class BundleError(RuntimeError):
    pass


def _skill_index() -> dict[str, Skill]:
    """Map skill short-name -> Skill. Fail loud on duplicates."""
    out: dict[str, Skill] = {}
    for sk in discover():
        if sk.name in out:
            raise BundleError(
                f"duplicate skill name '{sk.name}' in catalogue "
                f"({out[sk.name].path} vs {sk.path})"
            )
        out[sk.name] = sk
    return out


def _resolve_skill(reference: str, index: dict[str, Skill]) -> Skill:
    """Accept ``collection.expert_replay`` or ``expert_replay``."""
    short = reference.split(".")[-1]
    sk = index.get(short)
    if sk is None:
        raise BundleError(f"skill '{reference}' not found (resolved short='{short}')")
    return sk


def load(task: str) -> Bundle:
    """Find the task SKILL.md and its sibling bundle.yaml."""
    index = _skill_index()
    sk = index.get(task)
    if sk is None:
        raise BundleError(f"task '{task}' not found")
    if sk.frontmatter.get("kind") != "task":
        raise BundleError(
            f"skill '{task}' has kind='{sk.frontmatter.get('kind')}', expected 'task'"
        )
    bundle_path = sk.path.parent / "bundle.yaml"
    if not bundle_path.exists():
        raise BundleError(f"no bundle.yaml next to {sk.path}")
    raw = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "pipeline" not in raw:
        raise BundleError(f"{bundle_path} missing 'pipeline'")
    stages = [_parse_stage(entry) for entry in raw["pipeline"]]
    return Bundle(task=raw.get("task", task), source=bundle_path, stages=stages)


def _parse_stage(entry: dict[str, Any]) -> Stage:
    if not isinstance(entry, dict) or "stage" not in entry or "skill" not in entry:
        raise BundleError(f"malformed stage entry: {entry!r}")
    return Stage(
        stage=str(entry["stage"]),
        skill=str(entry["skill"]),
        params=dict(entry.get("params", {})),
        depends_on=[str(d) for d in entry.get("depends_on", []) or []],
        enabled=bool(entry.get("enabled", True)),
        farm=dict(entry.get("farm", {}) or {}),
    )


def execute(
    bundle: Bundle,
    *,
    only: str | None = None,
    dry_run: bool = False,
    on_stage_start=None,
    on_stage_end=None,
) -> dict[str, Any]:
    """Run the bundle sequentially.

    ``only``: run only that single stage (skip deps — caller's responsibility).
    ``dry_run``: print resolved skill + params, do not execute.
    """
    index = _skill_index()
    results: list[dict[str, Any]] = []
    failed_stages: set[str] = set()
    for stage in bundle.stages:
        if only and stage.stage != only:
            continue
        if not stage.enabled:
            results.append({"stage": stage.stage, "status": "skipped", "reason": "enabled=false"})
            continue
        blocked = [d for d in stage.depends_on if d in failed_stages]
        if blocked and only is None:
            results.append({
                "stage": stage.stage,
                "status": "blocked",
                "by": blocked,
            })
            failed_stages.add(stage.stage)
            continue
        sk = _resolve_skill(stage.skill, index)
        if on_stage_start:
            on_stage_start(stage, sk)
        if dry_run:
            results.append({
                "stage": stage.stage,
                "status": "dry-run",
                "skill": sk.name,
                "params": stage.params,
            })
            continue
        try:
            if stage.farm:
                result = _run_farmed(sk.name, stage)
            else:
                result = run_skill(sk.name, **stage.params)
            results.append({"stage": stage.stage, "status": "ok", "result": result})
        except NotImplementedError as exc:
            results.append({"stage": stage.stage, "status": "skeleton", "message": str(exc)})
        except (ValueError, RuntimeError) as exc:
            results.append({"stage": stage.stage, "status": "failed", "error": str(exc)})
            failed_stages.add(stage.stage)
        if on_stage_end:
            on_stage_end(stage, results[-1])
    return {"task": bundle.task, "source": str(bundle.source), "results": results}


def _run_farmed(skill_name: str, stage: Stage) -> dict[str, Any]:
    """Execute a stage in parallel via the Farm."""
    from roborsi.embodied.farm import run as farm_run
    farm_cfg = stage.farm
    workers = int(farm_cfg.get("workers", 4))
    episodes_per_worker = int(
        farm_cfg.get("episodes_per_worker")
        or stage.params.get("episodes", 25)
    )
    seed_start = int(farm_cfg.get("seed_start", stage.params.get("seed_start", 0)))
    # Pass all other stage.params through to the worker; seed_start + episodes
    # are managed by the farm.
    forwarded = {k: v for k, v in stage.params.items() if k not in ("seed_start", "episodes")}
    return farm_run(
        skill_name,
        workers=workers,
        episodes_per_worker=episodes_per_worker,
        seed_start=seed_start,
        params=forwarded,
    )
