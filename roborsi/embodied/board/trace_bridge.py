"""Board → live_trace / trace.db bridge, so sim monitoring flows through the
Board hub and lands on the feishu 8770 monitor.

``attach_sim_bridge`` — sim (cli_3role → task_runner → rollout): translates
``CH_SIM_STEP`` events back into the per-chat live_trace session carried in the
payload (``chat_id``), reproducing the projection live_trace would have written
had it appended the event directly.

Decoupled: ``embodied`` keeps no import-time dependency on ``channels`` — the
single cross-layer touch is a lazy import of ``channels.agent.feishu.live_trace``
inside the handler.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roborsi.embodied.board.constants import CH_SIM_STEP

if TYPE_CHECKING:
    from roborsi.embodied.board.board import Board

# Keys that LiveSession.append() owns; strip from forwarded data to avoid a
# kwargs collision.
_RESERVED = ("idx", "t", "kind")


def attach_sim_bridge(board: "Board") -> None:
    """Translate sim ``CH_SIM_STEP`` events into the per-chat live_trace session
    named in each payload (``chat_id``). The payload carries ``kind``
    (inner_tool_call / inner_tool_result) and the step fields; we re-append it on
    the owning session so live_trace's own dual-write projects it into trace.db."""

    def _forward(channel: str, data: dict[str, Any]) -> None:
        chat_id = data.get("chat_id")
        if not chat_id:
            return                                  # unattributable — drop
        from roborsi.channels.agent.feishu import live_trace

        payload = {k: v for k, v in data.items()
                   if k not in _RESERVED and k not in ("chat_id", "kind")}
        live_trace.get_session(chat_id).append(data["kind"], **payload)

    board.on(CH_SIM_STEP, _forward)
