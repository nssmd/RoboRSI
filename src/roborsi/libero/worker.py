"""One process shard for LIBERO short evaluation."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from roborsi.libero.catalog import SHORT_TASK_CATALOG
from roborsi.libero.config import ReleaseConfig, load_config


def skill_for_task(task_key: str) -> str:
    if task_key not in SHORT_TASK_CATALOG:
        raise ValueError(f"task is outside the LIBERO short catalog: {task_key}")
    from roborsi.embodied.skills import get_atomic_by_task_key

    atomic = get_atomic_by_task_key(task_key, backend="libero")
    if atomic is None:
        raise ValueError(f"task has no unique Atomic Skill profile: {task_key}")
    task_family = str(atomic.frontmatter.get("parent") or "").strip()
    if not task_family:
        raise ValueError(f"Atomic Skill has no Task Family parent: {atomic.name}")
    return task_family


def _all_records(campaign_root: Path):
    from roborsi.embodied.sim.libero.run_records import load_records

    rows = []
    for journal in sorted((campaign_root / "journals").glob("*.episodes.jsonl")):
        rows.extend(load_records(journal))
    return rows


def _runtime_env(
    config: ReleaseConfig,
    campaign_root: Path,
    release_id: str,
    *,
    workspace_root: Path | None = None,
) -> dict[str, str]:
    manifest = json.loads((campaign_root / "manifest.json").read_text(encoding="utf-8"))
    env = config.runtime_environment(os.environ)
    env.update(
        {
            "ROBORSI_LIBERO_ROLES": "1",
            "ROBORSI_ATOMIC_COMPOUND": "1",
            "ROBORSI_PROPOSAL_DIR": str(campaign_root / "proposals"),
            "ROBORSI_WORKSPACE": str(workspace_root or campaign_root / "workspace"),
            "ROBORSI_DATA_ROOT": str(campaign_root / "trajectories"),
            "ROBORSI_SELFEVO_FREEZE": "1" if manifest["mode"] == "fixed" else "0",
        }
    )
    return env


def run_assigned_tasks(
    config: ReleaseConfig,
    *,
    campaign_root: Path,
    seed: int,
    release_id: str,
    worker: int,
    task_keys: list[str],
    run_episode: Callable[..., dict[str, Any]] | None = None,
    workspace_root: Path | None = None,
    allow_changed_path: bool = False,
    journal_tag: str = "",
) -> Path:
    from roborsi.embodied.sim.libero.run_records import (
        EpisodeIdentity,
        EpisodeRecord,
        append_record,
        classify_infrastructure_exception,
        episode_workdir,
        reserve_attempt,
    )

    root = Path(campaign_root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if seed not in manifest["seeds"]:
        raise ValueError(f"seed is outside campaign protocol: {seed}")
    if len(task_keys) != len(set(task_keys)):
        raise ValueError("worker task list contains duplicates")
    for task in task_keys:
        skill_for_task(task)
    os.environ.update(_runtime_env(config, root, release_id, workspace_root=workspace_root))
    if run_episode is None:
        from roborsi.embodied.skills.executors.libero.runtime import run_libero_episode

        run_episode = run_libero_episode

    run_id = str(manifest["run_id"])
    suffix = f"-{journal_tag}" if journal_tag else ""
    journal = root / "journals" / f"seed-{seed}-worker-{worker}{suffix}.episodes.jsonl"
    existing = _all_records(root)
    solved = {row.identity.task_key for row in existing if row.category == "task_success"}
    successful_pairs = {
        (row.identity.task_key, int(row.identity.seed))
        for row in existing
        if row.category == "task_success"
    }
    terminal_pairs = {
        (row.identity.task_key, int(row.identity.seed))
        for row in existing
        if row.category in {"task_success", "task_failure", "implementation_failure"}
    }
    infrastructure = {
        "provider_failure",
        "transport_failure",
        "image_failure",
        "resource_failure",
    }
    for task in task_keys:
        task_seed = (task, seed)
        if task_seed in successful_pairs:
            continue
        if task in solved and not allow_changed_path:
            continue
        if task_seed in terminal_pairs and not allow_changed_path:
            continue
        os.environ["ROBORSI_TASK_KEY"] = task
        base_identity = EpisodeIdentity(
            run_id=run_id,
            task_key=task,
            seed=seed,
            shard=worker,
            attempt=1,
        )
        identity = reserve_attempt(journal, base_identity, resume_records=existing)
        workdir = episode_workdir(root / "episodes", identity)
        workdir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            episode = run_episode(
                skill=skill_for_task(task),
                task=task,
                seed=seed,
                tool_budget=config.evaluation.tool_budget,
                model=config.provider.model,
                workdir=workdir,
                backend="libero",
                episode_meta={
                    "run_id": run_id,
                    "task_key": task,
                    "seed": seed,
                    "shard": worker,
                    "attempt": identity.attempt,
                    "media_root": str(root / "media"),
                    "runtime_task_authoritative": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", "") or classify_infrastructure_exception(exc))
            usage = dict(getattr(exc, "usage", {}) or {})
            implementation = category == "implementation_failure"
            record = EpisodeRecord(
                identity=identity,
                category=category,
                success=False if implementation else None,
                outcome="implementation_exception" if implementation else None,
                elapsed_s=round(time.monotonic() - started, 3),
                recorded_at=datetime.now(timezone.utc).isoformat(),
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                vlm_calls=int(usage.get("vlm_calls", 0) or 0),
                unmetered_vlm_calls=int(usage.get("unmetered_vlm_calls", 0) or 0),
                detail=str(getattr(exc, "detail", "") or f"{type(exc).__name__}: {exc}"),
                preview_path=str(getattr(exc, "preview_path", "") or "") or None,
                release_id=release_id,
            )
            append_record(journal, record)
            existing.append(record)
            if category in infrastructure:
                break
            terminal_pairs.add((task, seed))
            continue

        meta = dict(episode.get("meta") or {})
        category = "task_success" if bool(episode.get("success")) else "task_failure"
        record = EpisodeRecord(
            identity=identity,
            category=category,
            success=bool(episode.get("success")),
            outcome=str(episode.get("outcome") or ""),
            elapsed_s=round(time.monotonic() - started, 3),
            recorded_at=datetime.now(timezone.utc).isoformat(),
            tool_calls=int(episode.get("steps", 0) or 0),
            physics_ticks=int(meta.get("physics_ticks", 0) or 0),
            prompt_tokens=int(meta.get("prompt_tokens", 0) or 0),
            completion_tokens=int(meta.get("completion_tokens", 0) or 0),
            total_tokens=int(meta.get("total_tokens", 0) or 0),
            vlm_calls=int(meta.get("vlm_calls", 0) or 0),
            unmetered_vlm_calls=int(meta.get("unmetered_vlm_calls", 0) or 0),
            vlm_time_s=float(meta.get("vlm_time_s", 0.0) or 0.0),
            perception_time_s=float(meta.get("perception_time_s", 0.0) or 0.0),
            action_time_s=float(meta.get("action_time_s", 0.0) or 0.0),
            recovery_time_s=float(meta.get("recovery_time_s", 0.0) or 0.0),
            recovery_reviewer_calls=int(meta.get("recovery_reviewer_calls", 0) or 0),
            recovery_reviewer_errors=int(meta.get("recovery_reviewer_errors", 0) or 0),
            role_usage=dict(meta.get("role_usage") or {}),
            planner_time_s=float(meta.get("planner_time_s", 0.0) or 0.0),
            reviewer_time_s=float(meta.get("reviewer_time_s", 0.0) or 0.0),
            orchestration_time_s=float(meta.get("orchestration_time_s", 0.0) or 0.0),
            code_backed_hit=bool(meta.get("code_backed_hit", False)),
            code_backed_call_count=int(meta.get("code_backed_call_count", 0) or 0),
            code_backed_tools=tuple(meta.get("code_backed_tools") or ()),
            video_path=str(meta.get("rollout_video") or meta.get("demo_video") or "") or None,
            preview_path=str(meta.get("preview_video") or "") or None,
            trajectory_path=str(meta.get("trajectory_path") or "") or None,
            detail=str(meta.get("media_error") or "") or None,
            release_id=release_id,
        )
        append_record(journal, record)
        existing.append(record)
        terminal_pairs.add((task, seed))
        if record.category == "task_success":
            solved.add(task)
            successful_pairs.add((task, seed))
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--worker", type=int, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--task-keys", nargs="+", required=True)
    args = parser.parse_args()
    run_assigned_tasks(
        load_config(args.config),
        campaign_root=args.campaign,
        seed=args.seed,
        release_id=args.release_id,
        worker=args.worker,
        task_keys=args.task_keys,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
