"""roborsi.agent.lifecycle — orchestrator agents (atomic / long-horizon)."""

from .atomic import (
    detect_state, status, ABSENT, SCAFFOLDED, COLLECTING,
    READY_TO_TRAIN, TRAINED, EVALED, ACTIVE,
)
from .scaffold import scaffold_atomic
from .driver import drive_atomic

__all__ = [
    "detect_state", "status", "scaffold_atomic", "drive_atomic",
    "ABSENT", "SCAFFOLDED", "COLLECTING",
    "READY_TO_TRAIN", "TRAINED", "EVALED", "ACTIVE",
]
