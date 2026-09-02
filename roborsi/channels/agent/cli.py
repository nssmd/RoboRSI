"""CLI channel — terminal stdin/stdout chat for demos and offline runs.

Each line of stdin is one user message. Agent output is streamed to
stdout. Cards are flattened to plain text via ``base._card_to_text``.

    roborsi start --channel=cli

Stop with Ctrl+C or EOF (Ctrl+D).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from roborsi.channels.agent.base import Channel, ChannelCtx


GREY = "\033[90m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RESET = "\033[0m"


class CliChannel(Channel):
    name = "cli"

    def __init__(self) -> None:
        self.ctx = ChannelCtx(chat_id=f"cli-{os.getpid()}",
                                sender_id="local")
        self._color = sys.stdout.isatty()
        self._history: list = []     # shared across all turns in this session

    def _wrap(self, code: str, s: str) -> str:
        return f"{code}{s}{RESET}" if self._color else s

    def run(self) -> None:
        monitor = os.environ.get("ROBORSI_MONITOR_URL",
                                   "http://localhost:8770")
        print(self._wrap(GREY,
            f"roborsi cli channel  chat_id={self.ctx.chat_id}\n"
            f"monitor: {monitor}/live/{self.ctx.chat_id}\n"
            "Type a request, ENTER to send. Ctrl+D / Ctrl+C to quit.\n"))
        while True:
            try:
                line = input(self._wrap(CYAN, "you> "))
            except (EOFError, KeyboardInterrupt):
                print()
                return
            text = line.strip()
            if not text:
                continue
            self.dispatch(self.ctx, text)

    def send_text(self, ctx: ChannelCtx, text: str) -> None:
        print(self._wrap(GREEN, "bot> ") + text)

    def upload_file(self, ctx: ChannelCtx, path: Path) -> str | None:
        print(self._wrap(GREY, f"      📎 {path}"))
        return str(path)
