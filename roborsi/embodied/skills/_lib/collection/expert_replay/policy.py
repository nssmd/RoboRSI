"""collection.expert_replay — drive a sim backend's expert for N seeds."""

from __future__ import annotations

from typing import Any

from roborsi.data.store import DataStore
from roborsi.embodied.agent_loop import get_backend


def run(
    task: str,
    backend: str = "robotwin",
    episodes: int = 1,
    seed_start: int = 0,
    config: dict[str, Any] | None = None,
    skill_label: str | None = None,
    plan_trace: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not task:
        raise ValueError("expert_replay.run requires 'task'")
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"backend '{backend}' unavailable: {reason}")
    label = skill_label or task
    store = DataStore()
    episodes_out: list[dict[str, Any]] = []
    with be.make_env(task, config or {}) as env:
        for i in range(episodes):
            seed = seed_start + i
            rollout = env.run_expert(seed=seed)
            written = store.write(
                rollout,
                skill=label,
                plan_trace=plan_trace,
                extra_meta={"collector": "expert_replay"},
            )
            episodes_out.append({
                "seed": seed,
                "success": rollout.success,
                "outcome": rollout.outcome,
                "run_id": written.run_id,
                "dir": str(written.dir),
                "frames": written.frames,
            })
    successes = sum(1 for e in episodes_out if e["success"])
    return {
        "skill": "expert_replay",
        "task": task,
        "backend": backend,
        "episodes": episodes_out,
        "total": len(episodes_out),
        "successes": successes,
        "success_rate": (successes / len(episodes_out)) if episodes_out else 0.0,
    }
