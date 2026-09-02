"""Ports between a chat platform and the Manager.

Whoever is talking to the agent IS the Manager — Telegram, Feishu, a browser or
a terminal only change how that conversation is transported. So there is no
per-platform agent: every adapter delivers into the same `ManagerEntry`, and
the Manager decides whether to answer directly or dispatch the
Planner→Engineer→Reviewer pipeline behind it.

Three ports, deliberately narrow (modelled on the hermes gateway, which runs 21
platforms behind an interface this size):

  InboundPort    a platform hands the Manager a message
  OutboundPort   the Manager replies — text, a rich card, or a file
  EventPort      the run streams progress out (tool calls, frames, verdicts)

The split matters because the three have different lifetimes. A reply is
request-scoped; events fire for minutes while a rollout runs and must reach
every attached surface at once, which is why EventPort is separate rather than
another OutboundPort method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Conversation:
    """One conversation, identified the same way everywhere.

    `chat_id` is the join key across the whole system: the sqlite trace store,
    the live monitor URL, the per-chat lock. Platforms map their native id onto
    it (Feishu open_chat_id, Telegram chat id, `cli-<pid>` for a terminal) so
    nothing downstream needs to know which platform it came from.
    """

    chat_id: str
    platform: str = "local"
    sender_id: str | None = None
    is_group: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reply:
    """What the Manager sends back.

    `card` carries structure a platform may render natively (Feishu cards,
    Telegram inline keyboards, HTML). Adapters that cannot render it fall back
    to `text`, so the Manager never has to ask what it is talking to.
    """

    text: str = ""
    card: dict[str, Any] | None = None
    files: list[Path] = field(default_factory=list)


class ManagerEntry(Protocol):
    """The single door into the agent. Every platform calls exactly this."""

    def handle(self, conv: Conversation, text: str) -> Reply:
        ...


class OutboundPort(Protocol):
    """How the Manager reaches a conversation mid-turn."""

    def send_text(self, conv: Conversation, text: str) -> None:
        ...

    def send_card(self, conv: Conversation, card: dict[str, Any]) -> None:
        ...

    def send_file(self, conv: Conversation, path: Path) -> str | None:
        ...


class InboundPort(Protocol):
    """A platform's receive loop. `run` blocks until stopped."""

    name: str

    def run(self, manager: ManagerEntry) -> None:
        ...

    def stop(self) -> None:
        ...


class EventPort(Protocol):
    """Progress from a running rollout: tool calls, frames, verdicts.

    Implementations must be cheap and must not raise — an event sink that
    throws would take down the run it is only observing.
    """

    def on_event(self, conv: Conversation, event: dict[str, Any]) -> None:
        ...

    def close(self) -> None:
        ...
