"""Parallel Farm — run the same lifecycle skill across multiple workers
with disjoint seed shards.

Motivation (from roborsi-story.md, 主线 B):
- Automated data collection runs in parallel on N sim instances.
- Each worker gets a disjoint seed range so there's zero overlap.
- Shared DataStore is safe because run_ids carry worker UUIDs.

Design:
- ``multiprocessing.Pool`` per farm invocation (not a persistent daemon;
  keeps the contract simple: farm returns when all workers exit).
- Workers invoke ``roborsi.embodied.skills.run(skill, **params)`` with
  their own ``seed_start``.
- Each worker gets its own Python process → isolated SAPIEN scenes, no
  CUDA context collisions (one env per process is the pattern RoboTwin
  expects).
- Failures in one worker don't kill the farm; we return a report with
  per-worker status.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerReport:
    worker_id: int
    seed_range: tuple[int, int]
    status: str                          # "ok" | "failed" | "skeleton"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _worker_entry(args: tuple[int, str, int, int, dict[str, Any]]) -> dict[str, Any]:
    worker_id, skill, seed_start, episodes, params = args
    # Each worker needs a fresh roborsi import tree; multiprocessing fork
    # shares Python state but SAPIEN needs its own init per process.
    from roborsi.embodied.skills import run as run_skill
    call = dict(params)
    call["seed_start"] = seed_start
    call["episodes"] = episodes
    try:
        result = run_skill(skill, **call)
        return {
            "worker_id": worker_id,
            "seed_range": [seed_start, seed_start + episodes],
            "status": "ok",
            "result": result,
        }
    except NotImplementedError as exc:
        return {
            "worker_id": worker_id,
            "seed_range": [seed_start, seed_start + episodes],
            "status": "skeleton",
            "error": str(exc),
        }
    except (ValueError, RuntimeError) as exc:
        return {
            "worker_id": worker_id,
            "seed_range": [seed_start, seed_start + episodes],
            "status": "failed",
            "error": str(exc),
        }


def run(
    skill: str,
    *,
    workers: int = 4,
    episodes_per_worker: int = 25,
    seed_start: int = 0,
    params: dict[str, Any] | None = None,
    spawn_method: str = "spawn",
) -> dict[str, Any]:
    """Farm out a collection skill across N workers.

    - ``skill``: lifecycle skill name (e.g. "expert_replay").
    - ``workers``: number of parallel processes.
    - ``episodes_per_worker``: each worker's episode count.
    - ``seed_start``: first seed; workers get disjoint shards starting here.
    - ``spawn_method``: "spawn" (default) avoids CUDA/SAPIEN fork pitfalls.
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if episodes_per_worker < 1:
        raise ValueError("episodes_per_worker must be >= 1")
    base_params = params or {}
    jobs = [
        (i, skill, seed_start + i * episodes_per_worker, episodes_per_worker, base_params)
        for i in range(workers)
    ]
    ctx = mp.get_context(spawn_method)
    with ctx.Pool(workers) as pool:
        reports = pool.map(_worker_entry, jobs)
    total_episodes = sum(_count_episodes(r) for r in reports)
    total_successes = sum(_count_successes(r) for r in reports)
    return {
        "skill": skill,
        "workers": workers,
        "workers_ok": sum(1 for r in reports if r["status"] == "ok"),
        "total_episodes": total_episodes,
        "total_successes": total_successes,
        "success_rate": (total_successes / total_episodes) if total_episodes else 0.0,
        "reports": reports,
    }


def _count_episodes(report: dict[str, Any]) -> int:
    return len(_result_episodes(report))


def _count_successes(report: dict[str, Any]) -> int:
    return sum(1 for e in _result_episodes(report) if e.get("success"))


def _result_episodes(report: dict[str, Any]) -> list[dict[str, Any]]:
    if report.get("status") != "ok":
        return []
    result = report.get("result") or {}
    eps = result.get("episodes") or []
    return eps if isinstance(eps, list) else []
