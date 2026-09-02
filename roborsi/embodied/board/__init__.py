"""Board package — per-embodiment unified state + pub/sub hub (看板)."""

from roborsi.embodied.board.board import Board, Subscriber
from roborsi.embodied.board.channels import (
    CH_CALIBRATION,
    CH_CONFIG,
    CH_FAULT_DETECTED,
    CH_FAULT_RESOLVED,
    CH_SESSION,
    WS_TYPES,
)
from roborsi.embodied.board.constants import Command, EpisodePhase, SessionState
from roborsi.embodied.board.consumer import Consumer, InputConsumer, OutputConsumer

__all__ = [
    "Board",
    "CH_CALIBRATION",
    "CH_CONFIG",
    "CH_FAULT_DETECTED",
    "CH_FAULT_RESOLVED",
    "CH_SESSION",
    "Command",
    "Consumer",
    "EpisodePhase",
    "InputConsumer",
    "OutputConsumer",
    "SessionState",
    "Subscriber",
    "WS_TYPES",
]
