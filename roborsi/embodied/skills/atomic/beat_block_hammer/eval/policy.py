"""atomic.beat_block_hammer.eval — eval + active_executor switch."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import home as _home_dir
from roborsi.embodied.skills import run as run_skill


_TASK = "beat_block_hammer"


def run(
    seeds: int = 20,
    seed_start: int = 1000,
    threshold: float = 0.70,
    checkpoint: str | None = None,
    executor: str = "pi0_checkpoint",
    **_: Any,
) -> dict[str, Any]:
    if executor == "pi0_checkpoint":
        if not checkpoint:
            checkpoint = _latest_checkpoint(_TASK)
        if not checkpoint:
            raise RuntimeError(
                f"no checkpoint provided and none found under "
                f"{_home_dir() / 'checkpoints' / _TASK}"
            )
    eval_result = run_skill(
        "success_rate",
        task=_TASK,
        executor=executor,
        seeds=seeds,
        seed_start=seed_start,
        checkpoint=checkpoint,
    )
    rate = float(eval_result.get("success_rate", 0.0))
    switched = rate >= threshold and executor == "pi0_checkpoint"
    if switched:
        _write_active_executor(_TASK, f"policy:{checkpoint}", rate)
    elif executor == "expert":
        # keep what's there; expert eval is a baseline not a switch trigger
        pass
    return {
        "skill": "atomic.beat_block_hammer.eval",
        "task": _TASK,
        "executor": executor,
        "checkpoint": checkpoint,
        "seeds": seeds,
        "successes": eval_result.get("successes"),
        "success_rate": rate,
        "threshold": threshold,
        "switched": switched,
        "report_path": eval_result.get("report_path"),
        "active_executor": _read_active_executor(_TASK),
    }


def _latest_checkpoint(task: str) -> str | None:
    """Find the most recent pretrained_model dir under ~/.roborsi/checkpoints/.

    LeRobot writes to <task>/<base>-<ts>/checkpoints/<step>/pretrained_model/.
    We resolve to that leaf so caller hands LeRobot the right path.
    """
    root = _home_dir() / "checkpoints"
    if not root.exists():
        return None
    candidates = list(root.rglob("pretrained_model"))
    if not candidates:
        return None
    # Prefer ones whose path mentions the task name; fall back to global newest.
    relevant = [p for p in candidates if task in str(p)]
    pool = relevant or candidates
    return str(max(pool, key=lambda p: p.stat().st_mtime))


def _state_path(task: str) -> Path:
    return _home_dir() / "atomic_state" / task / "active_executor.json"


def _write_active_executor(task: str, executor: str, rate: float) -> None:
    path = _state_path(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "task": task,
        "executor": executor,
        "since_ts": time.time(),
        "success_rate": rate,
    }, indent=2), encoding="utf-8")


def _read_active_executor(task: str) -> str:
    path = _state_path(task)
    if not path.exists():
        return "zeroshot"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("executor", "zeroshot")
    except (json.JSONDecodeError, OSError):
        return "zeroshot"
