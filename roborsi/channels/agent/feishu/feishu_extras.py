"""Reusable Feishu integration helpers:
  • SQLite-backed event dedup (so Feishu retries don't replay the same event)
  • Allowlist gate (FEISHU_ALLOWED_USERS=open_id_a,open_id_b...)
  • Processing reactions (🤔 on receive, removed on success, ❌ on failure)
  • Reply-in-thread instead of new-message-in-channel
  • Per-chat serial processing (threading.Lock per chat_id)

Kept thin so the channel adapter remains easy to inspect.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import urllib.request
from pathlib import Path
from typing import Any

from .bot_server import _get_tenant_token


# ────────────────────────────────────────────────────────────────────────
# DEDUP
# ────────────────────────────────────────────────────────────────────────

_DEDUP_DB = Path(os.environ.get("XDG_CONFIG_HOME",
                                  os.path.expanduser("~/.config"))) / "roborsi" / "feishu_dedup.db"


def _dedup_conn():
    _DEDUP_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DEDUP_DB))
    con.execute("CREATE TABLE IF NOT EXISTS events ("
                "event_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)")
    # Prune anything older than 24h to keep file small.
    import time
    con.execute("DELETE FROM events WHERE seen_at < ?", (int(time.time()) - 86400,))
    con.commit()
    return con


def is_duplicate_event(event_id: str | None) -> bool:
    """True if we've already processed this event_id (within 24h window).
    Stores the id on first call. Empty/None id → always False (no dedup)."""
    if not event_id:
        return False
    import time
    con = _dedup_conn()
    try:
        cur = con.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,))
        if cur.fetchone():
            return True
        con.execute("INSERT INTO events (event_id, seen_at) VALUES (?, ?)",
                    (event_id, int(time.time())))
        con.commit()
        return False
    finally:
        con.close()


# ────────────────────────────────────────────────────────────────────────
# ALLOWLIST
# ────────────────────────────────────────────────────────────────────────

def is_allowed(sender_id: str | None) -> bool:
    """Check FEISHU_ALLOWED_USERS. Empty env = allow everyone."""
    allow = os.environ.get("FEISHU_ALLOWED_USERS", "").strip()
    if not allow:
        return True
    return any(sender_id == s.strip() for s in allow.split(",") if s.strip())


# ────────────────────────────────────────────────────────────────────────
# REACTIONS
# ────────────────────────────────────────────────────────────────────────

def _reactions_enabled() -> bool:
    return os.environ.get("FEISHU_REACTIONS", "true").lower() not in {"false", "0", "no"}


def add_reaction(message_id: str, emoji_type: str = "ThinkingFace"
                  ) -> str | None:
    """POST a reaction to a message. Returns reaction_id (for later delete),
    or None on failure."""
    if not _reactions_enabled() or not message_id:
        return None
    token = _get_tenant_token()
    if not token:
        return None
    body = json.dumps({"reaction_type": {"emoji_type": emoji_type}}).encode()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            if d.get("code") == 0:
                return (d.get("data", {}).get("reaction", {})
                        .get("reaction_id"))
            return None
    except Exception:
        return None


def remove_reaction(message_id: str, reaction_id: str) -> bool:
    if not _reactions_enabled() or not message_id or not reaction_id:
        return False
    token = _get_tenant_token()
    if not token:
        return False
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}"
    req = urllib.request.Request(
        url, method="DELETE",
        headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("code") == 0
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────────
# REPLY-IN-THREAD
# ────────────────────────────────────────────────────────────────────────

def reply_in_thread(message_id: str, card: dict, msg_type: str = "interactive"
                     ) -> bool:
    """Reply to a specific message instead of posting a new chat message."""
    token = _get_tenant_token()
    if not token or not message_id:
        return False
    content = (json.dumps(card, ensure_ascii=False) if msg_type == "interactive"
                else json.dumps(card))
    body = json.dumps({"msg_type": msg_type, "content": content}).encode()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            return d.get("code") == 0
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────────
# PER-CHAT SERIAL LOCK
# ────────────────────────────────────────────────────────────────────────

_CHAT_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def chat_lock(chat_id: str) -> threading.Lock:
    """Get-or-create a Lock for this chat_id so messages from the same
    chat are processed serially."""
    with _LOCKS_GUARD:
        lk = _CHAT_LOCKS.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _CHAT_LOCKS[chat_id] = lk
        return lk
