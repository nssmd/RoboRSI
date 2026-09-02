"""Synchronous Channel interface for agent-driven chat.

A Channel is the bridge between a chat platform (Feishu, terminal, web)
and the synchronous ``bot_agent.handle_user_message`` loop. The agent
calls back into ``send_text`` / ``send_card`` / ``upload_file`` to relay
intermediate output and final replies; the channel decides how those
appear on its platform (e.g. Feishu cards vs terminal plain text).

The channel is responsible for the IO loop:
    channel.run()      # blocks; reads messages, dispatches to agent

Each inbound message becomes a ``ChannelCtx`` that the channel hands to
the agent; subsequent ``send_*`` calls use the same ctx to route the
reply back to the right conversation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ChannelCtx:
    """One conversation's routing context.

    ``chat_id`` is the stable key used everywhere: sqlite trace store,
    live monitor URL (`/live/<chat_id>`), per-chat lock. Feishu uses the
    Lark open_chat_id; the cli channel uses a synthetic id like
    ``cli-<pid>``.
    """
    chat_id: str
    sender_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Channel(ABC):
    """Synchronous channel base class."""

    name: str = "base"

    @abstractmethod
    def run(self) -> None:
        """Start the IO loop. Blocks until interrupted.

        For each inbound message, build a ``ChannelCtx`` and call
        ``self.dispatch(ctx, text)``. ``dispatch`` invokes the agent and
        relays the final reply via ``send_text(ctx, reply)`` — channels
        do not need to call ``send_text`` themselves after dispatch.
        """

    @abstractmethod
    def send_text(self, ctx: ChannelCtx, text: str) -> None:
        """Send plain text in the conversation identified by ``ctx``."""

    def send_card(self, ctx: ChannelCtx, card: dict[str, Any]) -> None:
        """Send a rich card; default falls back to extracting plain text."""
        text = _card_to_text(card)
        if text:
            self.send_text(ctx, text)

    def upload_file(self, ctx: ChannelCtx, path: Path) -> str | None:
        """Upload a file (e.g. demo mp4). Returns platform-specific id or
        None if the channel does not support uploads."""
        self.send_text(ctx, f"📎 {path}")
        return None

    def dispatch(self, ctx: ChannelCtx, text: str) -> str:
        """Hand a user message to the agent and send the reply back.

        If the channel maintains a ``_history`` list (e.g. CliChannel),
        it is threaded through ``handle_user_message`` so the agent sees
        a continuous conversation across multiple turns within one
        session — needed for tasks like "do click_bell across 5 seeds"
        where the agent should remember earlier failures."""
        from roborsi.channels.core.agent import (
            handle_user_message,
        )
        history = getattr(self, "_history", None)
        reply = handle_user_message(text, target_chat_id=ctx.chat_id,
                                       channel=self, ctx=ctx,
                                       history=history)
        if reply:
            self.send_text(ctx, reply)
        return reply


def _card_to_text(card: dict[str, Any]) -> str:
    """Strip a Feishu card down to a plain-text representation for
    channels that don't render cards (cli, raw web)."""
    parts: list[str] = []
    header = (card.get("header") or {}).get("title", {})
    if isinstance(header, dict) and header.get("content"):
        parts.append(f"━━ {header['content']} ━━")
    for el in card.get("elements") or []:
        if not isinstance(el, dict):
            continue
        if el.get("tag") == "div":
            text = (el.get("text") or {}).get("content")
            if text:
                parts.append(str(text))
        elif el.get("tag") == "action":
            for a in el.get("actions") or []:
                label = (a.get("text") or {}).get("content", "")
                url = a.get("url")
                if url:
                    parts.append(f"[{label}] {url}")
    return "\n".join(parts)
