"""Unified web layer for the board (看板) — the dashboards' architectural home.

Two dashboards, one data spine, served from here:
- **evo** (:8787, dash.argusbot.cn) — the self-evolution 看板 (:mod:`.evo_app` +
  :mod:`.page`, over :mod:`.evo_readers`).
- **cockpit** (:8795, sessions.argusbot.cn) — the React session cockpit
  (:mod:`.cockpit_app` serving ``frontend/web/dist``, over :mod:`.readers`).

Both read the same on-disk spine (trace.db via :mod:`roborsi.store.trace_db`,
campaign logs, wiki, review queues) through the shared readers here, instead of
the old duplicated readers in ``scripts/evo_dashboard.py`` + ``roborsi.webapi``.
:func:`serve` can run both ports in one process or just one (see :mod:`.server`).
"""

from roborsi.embodied.board.web.readers import (
    campaign_status,
    evolution_overview,
    list_sessions,
    manager_overview,
    session_turns,
    task_evolution,
    task_overview,
    task_progress,
)
from roborsi.embodied.board.web.server import main, serve

__all__ = [
    "campaign_status",
    "evolution_overview",
    "list_sessions",
    "main",
    "manager_overview",
    "serve",
    "session_turns",
    "task_evolution",
    "task_overview",
    "task_progress",
]
