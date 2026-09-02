"""roborsi.agent.lifecycle.atomic — atomic-task lifecycle state machine.

The orchestrator that drives an atomic skill from "absent" to "ACTIVE" (data
flywheel running). Implements `skills/agent/atomic_lifecycle/SKILL.md`.

Filesystem-as-state. Each phase reads/writes well-known paths, then the next
phase is decided by re-detecting state. No separate state file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import home as _home


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "embodied" / "skills"
ATOMIC_PHASES = ("zeroshot", "train", "eval", "reset_success", "reset_failure", "judge")


@dataclass(frozen=True)
class LifecycleState:
    name: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.name}({self.detail})" if self.detail else self.name


ABSENT = LifecycleState("ABSENT")
SCAFFOLDED = LifecycleState("SCAFFOLDED")
COLLECTING = LifecycleState("COLLECTING")
READY_TO_TRAIN = LifecycleState("READY_TO_TRAIN")
TRAINED = LifecycleState("TRAINED")
EVALED = LifecycleState("EVALED")
ACTIVE = LifecycleState("ACTIVE")


def detect_state(task_name: str, episode_target: int = 15) -> LifecycleState:
    atomic_dir = SKILLS_ROOT / "atomic" / task_name
    if not atomic_dir.exists():
        return ABSENT
    if not _is_scaffolded(atomic_dir):
        return ABSENT
    if _is_active(task_name):
        return ACTIVE
    has_ckpt = _latest_checkpoint(task_name) is not None
    has_eval = _latest_eval_report(task_name) is not None
    if has_eval:
        return EVALED
    if has_ckpt:
        return TRAINED
    successes = _count_successful_episodes(task_name)
    if successes >= episode_target:
        return READY_TO_TRAIN
    if successes > 0:
        return COLLECTING.__class__(name="COLLECTING", detail=f"{successes}/{episode_target}")
    return SCAFFOLDED


def _is_scaffolded(atomic_dir: Path) -> bool:
    if not (atomic_dir / "SKILL.md").exists():
        return False
    for phase in ("zeroshot", "train", "eval", "judge"):
        if not (atomic_dir / phase / "policy.py").exists():
            return False
    return True


def _is_active(task_name: str) -> bool:
    state_path = _home() / "atomic_state" / task_name / "active_executor.json"
    if not state_path.exists():
        return False
    try:
        executor = json.loads(state_path.read_text(encoding="utf-8")).get("executor", "")
    except (json.JSONDecodeError, OSError):
        return False
    return str(executor).startswith("policy:")


def _latest_checkpoint(task_name: str) -> Path | None:
    root = _home() / "checkpoints"
    if not root.exists():
        return None
    candidates = [p for p in root.rglob("pretrained_model") if task_name in str(p)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _latest_eval_report(task_name: str) -> Path | None:
    root = _home() / "evals" / task_name
    if not root.exists():
        return None
    reports = list(root.rglob("eval_report.json"))
    return max(reports, key=lambda p: p.stat().st_mtime) if reports else None


def _count_successful_episodes(task_name: str) -> int:
    data_dir = _home() / "data" / task_name
    if not data_dir.exists():
        return 0
    count = 0
    for meta_path in data_dir.rglob("meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("success") is True:
            count += 1
    return count


def status(task_name: str, episode_target: int = 15) -> dict[str, Any]:
    state = detect_state(task_name, episode_target=episode_target)
    return {
        "task": task_name,
        "state": state.name,
        "detail": state.detail,
        "scaffolded": (SKILLS_ROOT / "atomic" / task_name / "SKILL.md").exists(),
        "successes": _count_successful_episodes(task_name),
        "checkpoint": str(_latest_checkpoint(task_name) or ""),
        "eval_report": str(_latest_eval_report(task_name) or ""),
        "active_executor": _read_active_executor(task_name),
    }


def _read_active_executor(task_name: str) -> str:
    state_path = _home() / "atomic_state" / task_name / "active_executor.json"
    if not state_path.exists():
        return "zeroshot"
    try:
        return json.loads(state_path.read_text(encoding="utf-8")).get("executor", "zeroshot")
    except (json.JSONDecodeError, OSError):
        return "zeroshot"
