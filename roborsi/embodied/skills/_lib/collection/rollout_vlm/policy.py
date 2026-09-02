"""collection.rollout_vlm — VLM drives RoboTwin via real-world-shaped tools.

Loads the *task* SKILL.md frontmatter (kind: task) and uses its
``vlm_prompts.describe_scene`` + ``vlm_prompts.expected_on_success`` to
anchor the VLM. Falls back to the description if those fields are missing.

Saves each episode to the DataStore exactly like ``expert_replay``, with
``collector: rollout_vlm`` so downstream training can filter / mix.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from roborsi.data.store import DataStore
from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.skills import get as get_skill


def run(
    task: str,
    backend: str = "robotwin",
    episodes: int = 1,
    seed_start: int = 0,
    skill_label: str | None = None,
    model: str | None = None,
    tool_budget: int = 25,
    workdir: str | None = None,
    plan_trace: list[dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not task:
        raise ValueError("rollout_vlm requires 'task'")
    instruction, expected = _prompts_for(task)
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"backend '{backend}' unavailable: {reason}")
    label = skill_label or task
    store = DataStore()
    eps_out: list[dict[str, Any]] = []
    work = Path(workdir).expanduser() if workdir else Path("/tmp/roborsi-rollout")

    with be.make_env(task, {"require_depth": True}) as env:
        for i in range(episodes):
            seed = seed_start + i
            rollout = env.run_rollout(
                seed=seed,
                instruction=instruction,
                expected_on_success=expected,
                model=model,
                tool_budget=tool_budget,
                workdir=work,
            )
            written = store.write(
                rollout,
                skill=label,
                plan_trace=plan_trace,
                extra_meta={"collector": "rollout_vlm", "model": rollout.meta.get("model")},
            )
            eps_out.append({
                "seed": seed,
                "success": rollout.success,
                "outcome": rollout.outcome,
                "tool_calls": rollout.meta.get("tool_calls"),
                "run_id": written.run_id,
                "dir": str(written.dir),
                "frames": written.frames,
            })
    successes = sum(1 for e in eps_out if e["success"])
    return {
        "skill": "rollout_vlm",
        "task": task,
        "backend": backend,
        "episodes": eps_out,
        "total": len(eps_out),
        "successes": successes,
        "success_rate": (successes / len(eps_out)) if eps_out else 0.0,
    }


def _prompts_for(task: str) -> tuple[str, str]:
    """Pull instruction + success criterion out of the task SKILL.md frontmatter."""
    sk = get_skill(task)
    if sk is None:
        raise ValueError(f"unknown task '{task}' — no SKILL.md in catalogue")
    fm = sk.frontmatter or {}
    meta = fm.get("metadata") or {}
    prompts = meta.get("vlm_prompts") or {}
    instruction = (
        prompts.get("describe_scene")
        or prompts.get("instruction")
        or fm.get("description")
        or f"Complete the task '{task}'."
    )
    expected = (
        prompts.get("expected_on_success")
        or prompts.get("success")
        or "The task is visually complete."
    )
    return str(instruction).strip(), str(expected).strip()
