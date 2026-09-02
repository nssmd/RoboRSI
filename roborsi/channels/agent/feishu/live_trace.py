"""Per-chat live agent session — event log + interrupt flag.

The bot agent emits events as it works (thinking, tool_call, tool_result,
sim_progress). The HTML monitor polls /live_events to stream them to the
user. User clicks INTERRUPT → /interrupt POST sets flag → agent raises
AgentInterrupted at the next checkpoint.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class AgentInterrupted(Exception):
    """Raised when user requests interrupt mid-conversation."""


class LiveSession:
    """One chat_id worth of state. Events are appended as the agent runs;
    HTML reads via index pointer (?since=N)."""
    MAX_EVENTS = 500

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.events: deque[dict[str, Any]] = deque(maxlen=self.MAX_EVENTS)
        self._counter = 0
        self._lock = threading.Lock()
        self.interrupt_requested = False
        self.busy_since: float | None = None
        self.last_camera_frame: str | None = None    # path to JPG
        self.last_user_message: str = ""

    def append(self, kind: str, **payload) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            evt = {"idx": self._counter, "t": time.time(), "kind": kind,
                    **payload}
            self.events.append(evt)
        # Dual-write to sqlite — every event goes to `events` table so the
        # HTML monitor can replay full chat history across restarts; step-
        # shaped kinds additionally land in `steps` for analytics queries.
        try:
            self._db_dual_write(evt)
        except Exception:
            pass
        return evt

    def _db_dual_write(self, evt: dict[str, Any]) -> None:
        from roborsi.store import trace_db as _td
        # Generic event log (covers all kinds — used by /live).
        payload = {k: v for k, v in evt.items() if k not in ("idx", "t", "kind")}
        _td.append_event(self.chat_id, evt["kind"], payload, ts=evt.get("t"))
        # Step-shaped projection (used by /run, analytics, bench).
        kind = evt.get("kind")
        if kind not in ("tool_call", "tool_result",
                         "inner_tool_call", "inner_tool_result"):
            return
        layer = "inner" if kind.startswith("inner_") else "outer"
        run_id = evt.get("run_id") or (get_inner_run_id() if layer == "inner" else None)
        if kind in ("tool_call", "inner_tool_call"):
            _td.append_step(
                run_id=run_id, chat_id=self.chat_id, layer=layer,
                idx=evt.get("step"), tool=evt.get("name") or evt.get("tool"),
                args=evt.get("args"), reasoning=evt.get("reasoning"),
                ts=evt.get("t"))
        else:                                         # *_tool_result
            _td.append_step(
                run_id=run_id, chat_id=self.chat_id, layer=layer,
                idx=evt.get("step"), tool=evt.get("name") or evt.get("tool"),
                result_ok=evt.get("ok"),
                result_preview=evt.get("result_preview") or evt.get("preview"),
                ts=evt.get("t"))

    def get_since(self, since: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if e["idx"] > since]

    def request_interrupt(self) -> None:
        self.interrupt_requested = True
        self.append("interrupt_requested")

    def check_interrupt(self) -> None:
        """Call at every safe checkpoint. Raises AgentInterrupted if user
        clicked the INTERRUPT button."""
        if self.interrupt_requested:
            self.interrupt_requested = False
            raise AgentInterrupted(
                "User clicked INTERRUPT in the live monitor.")

    def set_busy(self, busy: bool) -> None:
        self.busy_since = time.time() if busy else None

    def update_camera_frame(self, path: str) -> None:
        self.last_camera_frame = path
        self.append("frame", path=path)


_SESSIONS: dict[str, LiveSession] = {}
_SESSIONS_LOCK = threading.Lock()


# Thread-local pointer to the LiveSession that should receive INNER (atomic
# skill) tool events. Set by task_runner before running a skill, cleared
# after. robotwin_agent checks this and emits per-step events so the
# `/live/<chat_id>` page can show inner progress live.
_inner_target = threading.local()


def set_inner_target(sess: "LiveSession | None") -> None:
    _inner_target.sess = sess


def get_inner_target() -> "LiveSession | None":
    return getattr(_inner_target, "sess", None)


# Inner run_id pointer — set by task_runner so emit_inner / dual-write can
# attribute inner-layer events to the correct run row.
_inner_run = threading.local()


def set_inner_run_id(run_id: str | None) -> None:
    _inner_run.run_id = run_id


def get_inner_run_id() -> str | None:
    return getattr(_inner_run, "run_id", None)


def emit_inner(kind: str, **payload) -> None:
    s = get_inner_target()
    if s is None:
        return
    # Tag events with run_id so the dual-write attributes them correctly.
    payload.setdefault("run_id", get_inner_run_id())
    # Route through the Board hub: the sim bridge translates the event back to
    # this session's append(kind, ...) — one monitoring centre for sim + hw.
    from roborsi.embodied.board.app_board import get_app_board
    from roborsi.embodied.board.constants import CH_SIM_STEP
    get_app_board().publish_sync(
        CH_SIM_STEP, {"chat_id": s.chat_id, "kind": kind, **payload})


def get_session(chat_id: str) -> LiveSession:
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(chat_id)
        if s is None:
            s = LiveSession(chat_id)
            _SESSIONS[chat_id] = s
        return s


def list_sessions() -> list[LiveSession]:
    with _SESSIONS_LOCK:
        return list(_SESSIONS.values())
