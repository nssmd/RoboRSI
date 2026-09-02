"""atomic.stack_bowls_bicoord.eval — eval + active_executor switch."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import home as _home_dir
from roborsi.embodied.skills import run as run_skill


_ATOMIC = "stack_bowls_bicoord"
_SIM_TASK = "stack_bowls"
_BACKEND = "bicoord"


def run(
    seeds: int = 5,
    seed_start: int = 1000,
    threshold: float = 0.05,
    checkpoint: str | None = None,
    executor: str = "pi0_checkpoint",
    **_: Any,
) -> dict[str, Any]:
    if executor == "pi0_checkpoint":
        if not checkpoint:
            checkpoint = _latest_checkpoint(_ATOMIC)
        if not checkpoint:
            raise RuntimeError(
                f"no checkpoint provided and none found under "
                f"{_home_dir() / 'checkpoints'} for atomic '{_ATOMIC}'"
            )
    eval_result = run_skill(
        "success_rate",
        task=_SIM_TASK,
        backend=_BACKEND,
        executor=executor,
        seeds=seeds,
        seed_start=seed_start,
        checkpoint=checkpoint,
    )
    rate = float(eval_result.get("success_rate", 0.0))
    switched = rate >= threshold and executor == "pi0_checkpoint"
    if switched:
        _write_active_executor(_ATOMIC, f"policy:{checkpoint}", rate)
    return {
        "skill": f"atomic.{_ATOMIC}.eval",
        "atomic": _ATOMIC,
        "sim_task": _SIM_TASK,
        "backend": _BACKEND,
        "executor": executor,
        "checkpoint": checkpoint,
        "seeds": seeds,
        "successes": eval_result.get("successes"),
        "success_rate": rate,
        "threshold": threshold,
        "switched": switched,
        "report_path": eval_result.get("report_path"),
        "active_executor": _read_active_executor(_ATOMIC),
    }


def _latest_checkpoint(atomic: str) -> str | None:
    root = _home_dir() / "checkpoints"
    if not root.exists():
        return None
    candidates = list(root.rglob("pretrained_model"))
    if not candidates:
        return None
    relevant = [p for p in candidates if atomic in str(p)]
    pool = relevant or candidates
    return str(max(pool, key=lambda p: p.stat().st_mtime))


def _state_path(atomic: str) -> Path:
    return _home_dir() / "atomic_state" / atomic / "active_executor.json"


def _write_active_executor(atomic: str, executor: str, rate: float) -> None:
    path = _state_path(atomic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "task": atomic,
        "executor": executor,
        "since_ts": time.time(),
        "success_rate": rate,
    }, indent=2), encoding="utf-8")


def _read_active_executor(atomic: str) -> str:
    path = _state_path(atomic)
    if not path.exists():
        return "zeroshot"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("executor", "zeroshot")
    except (json.JSONDecodeError, OSError):
        return "zeroshot"
