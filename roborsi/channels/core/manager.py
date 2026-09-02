"""The Manager: the single agent every conversation reaches.

Whoever is talking to the system is talking to the Manager. Telegram, Feishu, a
browser and a terminal are transports; none of them gets its own agent. The
Manager answers directly when it can, and dispatches the
Planner→Engineer→Reviewer pipeline when the request needs a robot.

The loop lives in `channels/core/agent.py`. It used to sit in
`channels/agent/feishu/`, where 2100 lines of general agent logic were filed
under one platform's name — only four of those lines actually touched Feishu,
all of them on the legacy `target_chat_id` path that predates channels. The
directory name was the bug: adding Telegram meant either copying the file or
importing the Manager out of a folder called `feishu`.
"""

from __future__ import annotations

from typing import Any

from .ports import Conversation, OutboundPort, Reply


class Manager:
    """Adapts the existing agent loop to the `ManagerEntry` protocol."""

    def __init__(self, outbound: OutboundPort | None = None,
                 max_hops: int = 15) -> None:
        self._outbound = outbound
        self._max_hops = max_hops
        # One history per chat_id: the Manager is stateful per conversation,
        # and that state must survive across turns on every platform, not just
        # the ones that happen to keep a session object around.
        self._history: dict[str, list] = {}

    def handle(self, conv: Conversation, text: str) -> Reply:
        from roborsi.channels.core.agent import handle_user_message

        history = self._history.setdefault(conv.chat_id, [])
        shim = _OutboundShim(self._outbound, conv) if self._outbound else None
        reply = handle_user_message(
            text,
            channel=shim,
            ctx=_CtxShim(conv) if shim else None,
            history=history,
            max_hops=self._max_hops,
        )
        return Reply(text=reply or "")

    def reset(self, conv: Conversation) -> None:
        self._history.pop(conv.chat_id, None)


class _CtxShim:
    """Present a Conversation as the legacy ChannelCtx the loop expects."""

    def __init__(self, conv: Conversation) -> None:
        self.chat_id = conv.chat_id
        self.sender_id = conv.sender_id
        self.extra = conv.extra


class _OutboundShim:
    """Present an OutboundPort as the legacy Channel the loop calls back into.

    The loop emits `send_text` / `send_card` / `upload_file` mid-turn; the new
    port names differ, so translate rather than change 2100 lines of call sites
    before the move is ready.
    """

    name = "port"

    def __init__(self, outbound: OutboundPort, conv: Conversation) -> None:
        self._out = outbound
        self._conv = conv

    def send_text(self, ctx: Any, text: str) -> None:
        self._out.send_text(self._conv, text)

    def send_card(self, ctx: Any, card: dict[str, Any]) -> None:
        self._out.send_card(self._conv, card)

    def upload_file(self, ctx: Any, path: Any) -> str | None:
        return self._out.send_file(self._conv, path)
