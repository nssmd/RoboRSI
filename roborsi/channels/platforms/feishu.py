"""Feishu adapter — a thin wrapper over the existing implementation.

The Feishu code (~4700 lines under channels/agent/feishu/) predates this layer
and works. Rewriting it to fit the ports would be churn with no user-visible
gain, so this registers it instead: existing deployments keep running, and new
platforms are written against `ManagerEntry` today rather than after a
migration.

The 2100-line `bot_agent.py` in that directory is not Feishu code — it is the
Manager, and only 21 of its lines mention Lark. Moving it into
`channels/core/` is the next step; nothing here depends on where it lives.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.ports import Conversation, ManagerEntry
from ..core.registry import PlatformEntry, registry


class FeishuAdapter:
    name = "feishu"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._inner = None

    def _channel(self):
        if self._inner is None:
            from roborsi.channels.agent.feishu.feishu_integration import FeishuChannel
            self._inner = FeishuChannel()
        return self._inner

    # ---- outbound -------------------------------------------------------
    def send_text(self, conv: Conversation, text: str) -> None:
        self._channel().send_text(_ctx(conv), text)

    def send_card(self, conv: Conversation, card: dict[str, Any]) -> None:
        self._channel().send_card(_ctx(conv), card)

    def send_file(self, conv: Conversation, path: Path) -> str | None:
        return self._channel().upload_file(_ctx(conv), path)

    # ---- inbound --------------------------------------------------------
    def run(self, manager: ManagerEntry) -> None:
        # The existing channel owns its own websocket loop and calls
        # handle_user_message directly, which is the same Manager this layer
        # wraps — so run it as-is rather than re-plumbing the socket.
        self._channel().run()

    def stop(self) -> None:
        inner = self._inner
        if inner is not None and hasattr(inner, "stop"):
            inner.stop()


def _ctx(conv: Conversation):
    from roborsi.channels.agent.base import ChannelCtx
    return ChannelCtx(chat_id=conv.extra.get("native_chat_id", conv.chat_id),
                      sender_id=conv.sender_id, extra=conv.extra)


def _available() -> bool:
    try:
        from roborsi.channels.agent.feishu import feishu_integration  # noqa: F401
        return True
    except ImportError:
        return False


registry.register(PlatformEntry(
    name="feishu",
    label="飞书",
    build=FeishuAdapter,
    available=_available,
    configured=lambda: bool(os.environ.get("FEISHU_APP_ID")
                            or os.environ.get("ROBORSI_FEISHU_APP_ID")),
    required_env=["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
    supports_cards=True,
    supports_files=True,
))
