"""Process-level Board singleton for sync sim producers.

The sim path (cli_3role → task_runner → rollout) has no event loop but still
needs one monitoring hub. ``get_app_board`` lazily builds a single Board and
wires the sim bridge on it (once), so every sim ``publish_sync(CH_SIM_STEP,
...)`` is translated into live_trace / trace.db.
"""
from __future__ import annotations

import threading

from roborsi.embodied.board.board import Board

_APP_BOARD: Board | None = None
_LOCK = threading.Lock()


def get_app_board() -> Board:
    """Return the process-wide Board, building + bridging it on first call."""
    global _APP_BOARD
    if _APP_BOARD is not None:
        return _APP_BOARD
    with _LOCK:
        if _APP_BOARD is None:
            board = Board()
            from roborsi.embodied.board.trace_bridge import attach_sim_bridge
            attach_sim_bridge(board)
            _APP_BOARD = board
    return _APP_BOARD
