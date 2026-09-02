"""Terminal adapter. The reference implementation — ~90 lines.

If a platform needs much more than this, the extra belongs in the Manager, not
in the adapter. An adapter's whole job is transport: read a line, hand it to
the Manager, render what comes back.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ..core.ports import Conversation, ManagerEntry
from ..core.registry import PlatformEntry, registry


class CliAdapter:
    name = "cli"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._conv = Conversation(
            chat_id=cfg.get("chat_id") or f"cli-{os.getpid()}",
            platform="cli",
            sender_id=os.environ.get("USER", "local"),
        )
        self._stop = False

    # ---- outbound -------------------------------------------------------
    def send_text(self, conv: Conversation, text: str) -> None:
        print(text, flush=True)

    def send_card(self, conv: Conversation, card: dict[str, Any]) -> None:
        # No native cards in a terminal; flatten rather than drop, so a
        # terminal user sees the same content a Feishu user would.
        print(_flatten_card(card), flush=True)

    def send_file(self, conv: Conversation, path: Path) -> str | None:
        print(f"[file] {path}", flush=True)
        return str(path)

    # ---- inbound --------------------------------------------------------
    def run(self, manager: ManagerEntry) -> None:
        print(f"RoboRSI · {self._conv.chat_id}   (Ctrl-D 退出)", flush=True)
        while not self._stop:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line in ("/quit", "/exit"):
                return
            reply = manager.handle(self._conv, line)
            if reply.text:
                print(reply.text, flush=True)
            for f in reply.files:
                print(f"[file] {f}", flush=True)

    def stop(self) -> None:
        self._stop = True


def _flatten_card(card: dict[str, Any]) -> str:
    """Best-effort text rendering of a card, for surfaces without rich UI."""
    out: list[str] = []
    header = card.get("header") or {}
    title = (header.get("title") or {}).get("content")
    if title:
        out.append(f"── {title} ──")
    for el in card.get("elements") or []:
        text = el.get("text")
        if isinstance(text, dict) and text.get("content"):
            out.append(str(text["content"]))
        elif isinstance(text, str):
            out.append(text)
    return "\n".join(out) or str(card)


registry.register(PlatformEntry(
    name="cli",
    label="Terminal",
    build=CliAdapter,
    supports_cards=False,
    supports_files=True,
))
