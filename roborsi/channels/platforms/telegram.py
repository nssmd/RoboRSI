"""Telegram adapter — and the mobile client, for free.

There is no separate phone app to build: Telegram already is one, on both
platforms, with push, media and offline queueing solved. That is the whole
reason to reach for it before writing a responsive dashboard.

Long polling rather than webhooks: the machine this runs on sits behind a
tunnel with no stable public URL, and polling needs no inbound port. The cost
is one idle HTTP request at a time, which is nothing next to a rollout.

Only `requests` is required — the official SDK pulls an async stack this
codebase does not otherwise use.
"""

from __future__ import annotations

import html
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..core.ports import Conversation, ManagerEntry
from ..core.progress import RunProgress
from ..core.registry import PlatformEntry, registry

API = "https://api.telegram.org/bot{token}/{method}"
# Telegram hard-caps a message at 4096 chars; agent replies routinely exceed it.
LIMIT = 4000
# Telegram throttles edits per chat; a rollout emits events far faster than
# that, so coalesce rather than let the API start returning 429.
EDIT_EVERY_S = 3.0
TRACE_POLL_S = 1.0


class TelegramAdapter:
    name = "telegram"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._token = cfg.get("token") or os.environ.get("ROBORSI_TELEGRAM_TOKEN", "")
        allow = cfg.get("allowed_chats") or os.environ.get("ROBORSI_TELEGRAM_CHATS", "")
        # An open bot is an open shell: the Manager can run skills and read the
        # repo, so unlisted chats are ignored rather than served.
        self._allowed = {c.strip() for c in str(allow).split(",") if c.strip()}
        self._offset = 0
        self._stop = False
        # chat_id -> (progress, message_id, last_edit_ts)
        self._runs: dict[str, tuple[RunProgress, int | None, float]] = {}

    # ---- transport ------------------------------------------------------
    def _call(self, method: str, **params) -> dict:
        import requests
        r = requests.post(API.format(token=self._token, method=method),
                          json=params, timeout=70)
        return r.json()

    def _allowed_chat(self, chat_id: str) -> bool:
        return not self._allowed or chat_id in self._allowed

    # ---- outbound -------------------------------------------------------
    def send_text(self, conv: Conversation, text: str) -> None:
        for chunk in _split(text, LIMIT):
            self._call("sendMessage", chat_id=conv.chat_id, text=chunk,
                       disable_web_page_preview=True)

    def send_card(self, conv: Conversation, card: dict[str, Any]) -> None:
        self.send_text(conv, _card_to_markdown(card))

    def send_file(self, conv: Conversation, path: Path) -> str | None:
        import requests
        p = Path(path)
        if not p.is_file():
            return None
        # Video as video, everything else as a document — a demo mp4 should be
        # playable inline on a phone, not an attachment to download first.
        method, field = ("sendVideo", "video") if p.suffix.lower() in (".mp4", ".mov") \
            else ("sendDocument", "document")
        with p.open("rb") as fh:
            r = requests.post(API.format(token=self._token, method=method),
                              data={"chat_id": conv.chat_id},
                              files={field: (p.name, fh)}, timeout=300)
        return str(path) if r.ok else None

    # ---- live progress ---------------------------------------------------
    def on_event(self, conv: Conversation, event: dict[str, Any]) -> None:
        """Fold an event into the chat's progress card and edit it in place.

        One message per run rather than one per event: a rollout makes dozens of
        tool calls, and a chat that scrolls them all is unreadable and rate
        limited. The card is only rewritten when something a user could act on
        changed, and at most once every EDIT_EVERY_S.
        """
        native = conv.extra.get("native_chat_id") or conv.chat_id.removeprefix("tg-")
        prog, msg_id, last = self._runs.get(conv.chat_id, (RunProgress(), None, 0.0))
        if not prog.feed(event):
            self._runs[conv.chat_id] = (prog, msg_id, last)
            return

        now = time.time()
        due = prog.finished or (now - last) >= EDIT_EVERY_S
        if not due:
            self._runs[conv.chat_id] = (prog, msg_id, last)
            return

        text = prog.render()
        if msg_id is None:
            r = self._call("sendMessage", chat_id=native, text=text)
            msg_id = ((r or {}).get("result") or {}).get("message_id")
        else:
            self._call("editMessageText", chat_id=native,
                       message_id=msg_id, text=text)
        self._runs[conv.chat_id] = (prog, msg_id, now)

    def _start_trace(self, conv: Conversation) -> None:
        """Tail this chat's LiveSession until the run reports it is done.

        LiveSession offers no subscribe hook, and adding one would mean editing
        the agent loop's hot path to serve a display. Polling once a second is
        an index compare.
        """
        from roborsi.channels.agent.feishu.live_trace import get_session

        def tail() -> None:
            sess = get_session(conv.chat_id)
            cursor = 0
            deadline = time.time() + 3600
            while time.time() < deadline:
                for ev in sess.get_since(cursor):
                    cursor += 1
                    self.on_event(conv, ev)
                prog = self._runs.get(conv.chat_id, (None,))[0]
                if prog is not None and prog.finished:
                    return
                time.sleep(TRACE_POLL_S)

        threading.Thread(target=tail, name=f"tg-trace-{conv.chat_id}",
                         daemon=True).start()

    # ---- inbound --------------------------------------------------------
    def run(self, manager: ManagerEntry) -> None:
        import requests

        while not self._stop:
            try:
                r = requests.get(API.format(token=self._token, method="getUpdates"),
                                 params={"offset": self._offset, "timeout": 50},
                                 timeout=70)
                updates = r.json().get("result") or []
            except requests.RequestException:
                # Poll again rather than exit: a dropped long-poll is routine.
                time.sleep(3)
                continue

            for upd in updates:
                self._offset = max(self._offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or upd.get("edited_message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if not text or not chat_id or not self._allowed_chat(chat_id):
                    continue
                conv = Conversation(
                    chat_id=f"tg-{chat_id}", platform="telegram",
                    sender_id=str((msg.get("from") or {}).get("id") or ""),
                    is_group=chat.get("type") in ("group", "supergroup"),
                    extra={"native_chat_id": chat_id},
                )
                # The Manager owns routing; the adapter only carries the text.
                self._call("sendChatAction", chat_id=chat_id, action="typing")
                self._runs.pop(conv.chat_id, None)   # fresh card per turn
                self._start_trace(conv)
                reply = manager.handle(conv, text)
                if reply.text:
                    self._send_raw(chat_id, reply.text)
                for f in reply.files:
                    self.send_file(Conversation(chat_id=chat_id, platform="telegram"), f)

    def _send_raw(self, chat_id: str, text: str) -> None:
        for chunk in _split(text, LIMIT):
            self._call("sendMessage", chat_id=chat_id, text=chunk,
                       disable_web_page_preview=True)

    def stop(self) -> None:
        self._stop = True


def _split(text: str, limit: int) -> list[str]:
    """Chunk on line boundaries so a tool trace is not cut mid-line."""
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            out.append(buf)
            buf = ""
        buf += line
    if buf:
        out.append(buf)
    return out


def _card_to_markdown(card: dict[str, Any]) -> str:
    header = ((card.get("header") or {}).get("title") or {}).get("content")
    parts = [f"*{html.escape(str(header))}*"] if header else []
    for el in card.get("elements") or []:
        t = el.get("text")
        if isinstance(t, dict) and t.get("content"):
            parts.append(str(t["content"]))
        elif isinstance(t, str):
            parts.append(t)
    return "\n".join(parts) or str(card)


def _available() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


registry.register(PlatformEntry(
    name="telegram",
    label="Telegram (含移动端)",
    build=TelegramAdapter,
    available=_available,
    configured=lambda: bool(os.environ.get("ROBORSI_TELEGRAM_TOKEN")),
    required_env=["ROBORSI_TELEGRAM_TOKEN"],
    install_hint="pip install requests",
    supports_cards=False,
    supports_files=True,
))
