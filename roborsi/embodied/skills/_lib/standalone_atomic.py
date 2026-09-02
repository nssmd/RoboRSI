"""Shared helpers for atomic ``.zeroshot`` skills.

``ensure_env`` yields a live sim env whether the caller is a long-horizon
runner (passing a live ``env``) or a standalone diagnostic invocation
(reviewer / ``run_skill``, ``env is None`` → spawn one). ``_prompts_for``
reads the fallback instruction + expected_on_success from a task's
SKILL.md frontmatter (used when no persistent plan.md exists yet).

The cold-start DataStore collector (``run_standalone``) has been removed:
persistent recipes now live in each task's ``<skill_dir>/plan.md`` and
``~/.roborsi`` holds runtime data only. ``.zeroshot`` policies read
that plan.md (falling back to ``_prompts_for``) and run a rollout episode
for live diagnostics without persisting trajectories.

Usage in atomic/<task>/zeroshot/policy.py:

    from roborsi.embodied.skills._lib.standalone_atomic import (
        ensure_env, _prompts_for,
    )
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.skills import get as get_skill


@contextmanager
def ensure_env(env, *, spawn_task: str, backend: str = "bicoord",
               require_depth: bool = True):
    """Yield a live sim env for a custom atomic ``.zeroshot`` skill.

    Two modes, one place:
      - long-horizon: a live ``env`` is passed in → yield it unchanged and leave
        it OPEN (the caller owns its lifecycle). Byte-identical to passing the
        env straight through.
      - standalone (``env is None``): spawn one from ``spawn_task`` via the
        backend and close it on exit.

    Removes the ``if env is None: raise`` boilerplate that every custom atomic
    skill kept re-implementing (or crashing on when invoked standalone by the
    reviewer / ``run_skill``). The backend's ``make_env`` context manager owns
    reset/teardown — mirrors the already-validated standalone siblings.
    """
    if env is not None:
        yield env
        return
    if not spawn_task:
        raise ValueError("standalone invocation needs spawn_task")
    backend_obj = get_backend(backend)
    ok, reason = backend_obj.available()
    if not ok:
        raise RuntimeError(f"backend '{backend}' unavailable: {reason}")
    with backend_obj.make_env(spawn_task, {"require_depth": require_depth}) as live:
        yield live


def _prompts_for(task: str) -> tuple[str, str]:
    sk = get_skill(task)
    if sk is None:
        raise ValueError(f"task SKILL.md not found for '{task}'")
    fm = sk.frontmatter or {}
    meta = fm.get("metadata") or {}
    prompts = meta.get("vlm_prompts") or {}
    instruction = (
        prompts.get("instruction")
        or prompts.get("describe_scene")
        or fm.get("description")
        or f"Complete the task '{task}'."
    )
    expected = (
        prompts.get("expected_on_success")
        or "The task is visually complete."
    )
    return str(instruction).strip(), str(expected).strip()


def _instruction_for(task: str) -> tuple[str, str]:
    """Resolve (instruction, expected_on_success) for a rollout episode.

    Prefers the task's persistent ``<skill_dir>/plan.md`` (refined across
    runs, shipped with the skill). Falls back to the SKILL.md frontmatter
    prompts via ``_prompts_for`` when no plan.md exists yet. The
    expected_on_success predicate always comes from the frontmatter (the
    plan.md carries the full recipe as the instruction).
    """
    fallback_instruction, expected = _prompts_for(task)
    from roborsi.agents.task_wiki import _task_skill_dir
    plan_path = _task_skill_dir(task) / "plan.md"
    if plan_path.exists():
        plan_md = plan_path.read_text(encoding="utf-8").strip()
        if plan_md:
            return plan_md, expected
    return fallback_instruction, expected


def run_zeroshot_diagnostic(
    task: str,
    *,
    env=None,
    seed: int | None = None,
    spawn_task: str | None = None,
    backend: str = "bicoord",
    tool_budget: int = 14,
    model: str | None = None,
    workdir: str | None = None,
    require_depth: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """Run ONE rollout episode for an atomic ``.zeroshot`` skill as a live
    diagnostic (reviewer / ``run_skill``).

    Reads the task's persistent plan.md as the instruction (fallback:
    SKILL.md prompts), opens an env via ``ensure_env`` (reuses a live one if
    passed), and runs a single rollout episode. Does NOT persist the
    trajectory — cold-start collection lives in plan.md now, and
    ``~/.roborsi`` is runtime-only. Returns the same dict shape the old
    ``run_standalone`` did so callers/tests stay compatible.
    """
    from roborsi.embodied.agent_loop.rollout import run_rollout
    instruction, expected = _instruction_for(task)
    work = (Path(workdir).expanduser() if workdir
            else Path(f"/tmp/roborsi-zeroshot/{task}"))
    ep_seed = int(seed or 0)
    with ensure_env(env, spawn_task=spawn_task or task, backend=backend,
                    require_depth=require_depth) as live:
        # ensure_env's standalone make_env() yields the env WITHOUT resetting,
        # so RoboTwinEnv._impl is still None (it is populated only by reset()).
        # run_rollout immediately snapshots via env._impl.get_obs(),
        # which would AttributeError on None. The LH/passed-env path hands in an
        # already-reset env (_impl set) and is skipped. Reset here so the
        # standalone reviewer/run_skill path has a live impl before the episode.
        if getattr(live, "_impl", None) is None:
            live.reset(ep_seed)
        result = run_rollout(
            live, seed=ep_seed, task_name=task,
            instruction=instruction, expected_on_success=expected,
            model=model, tool_budget=tool_budget, workdir=work,
        )
    entry = {
        "seed": ep_seed,
        "success": result.success,
        "outcome": result.outcome,
        "tool_calls": result.rollout.meta.get("tool_calls"),
        "vlm_trace": result.trace,
    }
    return {
        "skill": f"atomic.{task}.zeroshot",
        "task": task,
        "success": result.success,
        "outcome": result.outcome,
        "trace": result.trace,
        "episodes": [entry],
        "total": 1,
        "successes": 1 if result.success else 0,
        "success_rate": 1.0 if result.success else 0.0,
    }
