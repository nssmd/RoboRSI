"""roborsi.agent.lifecycle.driver — `atomic spin <name>`.

Closed-loop state machine driver. Detects current state, dispatches the next
phase, repeats. Stops at ACTIVE (data flywheel running) or human-intervention
needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from roborsi.embodied.skills import run as run_skill

from .atomic import (
    detect_state, status,
    ABSENT, SCAFFOLDED, COLLECTING, READY_TO_TRAIN, TRAINED, EVALED, ACTIVE,
)


console = Console()


def drive_atomic(
    task_name: str,
    sim_task: str,
    backend: str = "bicoord",
    seeds: list[int] | None = None,
    success_target: float = 0.40,
    episode_target: int = 15,
    train_steps: int = 2000,
    long_horizon_wrapper: str | None = None,
    max_iterations: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Drive an atomic task to ACTIVE state.

    Args:
        task_name: must already be scaffolded.
        sim_task: BiCoord task to spawn for collection / eval.
        seeds: seed pool for collection. Default range(1, 26).
        success_target: eval threshold for active_executor switch.
        episode_target: minimum success episodes before triggering train.
        long_horizon_wrapper: if set, dispatch via this long-horizon task's
            execute (e.g. "handover_block_bicoord"). Else the atomic must
            support spawn_task in zeroshot.
        max_iterations: safety cap on the outer loop.
        dry_run: print actions without invoking.

    Returns the final state dict.
    """
    seeds = list(seeds or range(1, 26))
    seed_pool = iter(seeds)
    history: list[dict[str, Any]] = []

    for it in range(max_iterations):
        cur = detect_state(task_name, episode_target=episode_target)
        snapshot = status(task_name, episode_target=episode_target)
        history.append({"iteration": it, "state": cur.name, **{k: v for k, v in snapshot.items() if k != "state"}})
        console.print(f"[cyan]iter {it}[/] state={cur.name} successes={snapshot['successes']}/{episode_target}")

        if cur.name == ABSENT.name:
            return {"ok": False, "reason": "atomic not scaffolded — run `atomic new` first",
                    "history": history}

        if cur.name == ACTIVE.name:
            console.print("[green]✓ ACTIVE — flywheel running[/]")
            return {"ok": True, "state": "ACTIVE", "history": history,
                    "active_executor": snapshot["active_executor"]}

        if cur.name in (SCAFFOLDED.name, "COLLECTING"):
            seed = next(seed_pool, None)
            if seed is None:
                return {"ok": False, "reason": "exhausted seeds without enough successes",
                        "successes": snapshot["successes"], "history": history}
            _do_collect(task_name, sim_task, backend, int(seed),
                        long_horizon_wrapper=long_horizon_wrapper, dry_run=dry_run)
            continue

        if cur.name == READY_TO_TRAIN.name:
            _do_train(task_name, train_steps=train_steps, dry_run=dry_run)
            continue

        if cur.name == TRAINED.name:
            _do_eval(task_name, success_target=success_target, dry_run=dry_run)
            continue

        if cur.name == EVALED.name:
            # Either the eval already wrote active_executor (→ ACTIVE next iter)
            # or it didn't reach threshold. Fall back to more collection.
            console.print("[yellow]eval below threshold — collecting more data[/]")
            seed = next(seed_pool, None)
            if seed is None:
                return {"ok": False, "reason": "trained policy below threshold; no more seeds to collect",
                        "history": history}
            _do_collect(task_name, sim_task, backend, int(seed),
                        long_horizon_wrapper=long_horizon_wrapper, dry_run=dry_run)
            continue

        return {"ok": False, "reason": f"unknown state {cur.name}", "history": history}

    return {"ok": False, "reason": f"hit max_iterations={max_iterations}", "history": history}


def _do_collect(task_name: str, sim_task: str, backend: str, seed: int,
                long_horizon_wrapper: str | None, dry_run: bool) -> dict[str, Any]:
    if long_horizon_wrapper:
        target = f"{long_horizon_wrapper}.execute"
        params = {"seed": seed, "backend": backend}
        console.print(f"  → collect via long-horizon `{target}` seed={seed}")
    else:
        target = f"{task_name}.zeroshot"
        params = {"seed": seed, "spawn_task": sim_task, "backend": backend}
        console.print(f"  → collect via atomic `{target}` seed={seed}")
    if dry_run:
        return {"dry_run": True, "target": target, "params": params}
    result = run_skill(target, **params)
    return {"target": target, "seed": seed, "result_summary":
            {k: result.get(k) for k in ("success", "outcome", "tool_calls")}}


def _do_train(task_name: str, train_steps: int, dry_run: bool) -> dict[str, Any]:
    console.print(f"  → train `{task_name}.train` steps={train_steps}")
    if dry_run:
        return {"dry_run": True}
    return run_skill(f"{task_name}.train", steps=train_steps)


def _do_eval(task_name: str, success_target: float, dry_run: bool) -> dict[str, Any]:
    console.print(f"  → eval `{task_name}.eval` threshold={success_target}")
    if dry_run:
        return {"dry_run": True}
    return run_skill(f"{task_name}.eval", threshold=success_target,
                     executor="pi0_checkpoint", seeds=5, seed_start=1000)
