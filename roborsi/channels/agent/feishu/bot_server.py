"""Feishu bot event server.

Listens for Feishu event subscriptions (URL verification + message events),
parses /audit /approve /reject /list /help commands from chat, dispatches
to handle_command, and sends the rich card reply back via Feishu API.

Setup:
  1. Create a Feishu App at https://open.feishu.cn/
  2. Add bot capability + im:message:send_as_bot permission scope
  3. Configure event subscription:
       - Request URL: http(s)://your-host:PORT/feishu/event
       - Subscribe to: im.message.receive_v1
       - Encrypt key (optional): set FEISHU_ENCRYPT_KEY
       - Verification token: set FEISHU_VERIFICATION_TOKEN
  4. Set env: FEISHU_APP_ID, FEISHU_APP_SECRET (for posting replies)
  5. Run: roborsi-sim skill feishu-bot --port 9876

For one-way notifications (push-only), only FEISHU_WEBHOOK_URL is needed
(no app/bot setup); use push_proposal_to_feishu() directly.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
import urllib.request


_TOKEN_CACHE: dict[str, Any] = {"value": None, "expires_at": 0}


def _get_tenant_token() -> str | None:
    """Fetch tenant access token via Feishu Open API. Cached."""
    now = time.time()
    if _TOKEN_CACHE["value"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["value"]
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
    except Exception as e:
        print(f"[feishu-bot] token fetch failed: {e}")
        return None
    if d.get("code") != 0:
        print(f"[feishu-bot] token error: {d}")
        return None
    _TOKEN_CACHE["value"] = d["tenant_access_token"]
    _TOKEN_CACHE["expires_at"] = now + int(d.get("expire", 7200))
    return _TOKEN_CACHE["value"]


def _send_card(chat_id: str, card: dict) -> bool:
    token = _get_tenant_token()
    if not token:
        print("[feishu-bot] no token; cannot reply")
        return False
    body = json.dumps({
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            if d.get("code") != 0:
                print(f"[feishu-bot] send error: {d}")
                return False
        return True
    except Exception as e:
        print(f"[feishu-bot] send failed: {e}")
        return False


def _send_text(chat_id: str, text: str) -> bool:
    """Send a plain text message (no card). Used by the agent reply path."""
    token = _get_tenant_token()
    if not token:
        return False
    body = json.dumps({
        "receive_id": chat_id, "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            if d.get("code") != 0:
                print(f"[feishu-bot] send text error: {d}")
                return False
        return True
    except Exception as e:
        print(f"[feishu-bot] send text failed: {e}")
        return False



class _BotHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_POST(self):
        if self.path != "/feishu/event":
            self._send(404, "not found"); return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            evt = json.loads(body)
        except json.JSONDecodeError:
            self._send(400, "bad json"); return

        # URL verification challenge (Feishu setup).
        if evt.get("type") == "url_verification":
            self._send_json({"challenge": evt.get("challenge", "")})
            return

        # Token check (legacy verification token from old Feishu V1 events).
        token = evt.get("token") or evt.get("header", {}).get("token")
        expected = os.environ.get("FEISHU_VERIFICATION_TOKEN")
        if expected and token and token != expected:
            self._send(403, "bad token"); return

        # Persistent dedup — Feishu retries the same event on 5xx;
        # without dedup we'd run the task N times.
        from roborsi.channels.agent.feishu.feishu_extras import (
            is_duplicate_event,
        )
        evt_id = (evt.get("header", {}).get("event_id")
                  or evt.get("uuid"))
        if is_duplicate_event(evt_id):
            self._send_json({"ok": True, "dedup": True}); return

        # V2 message event.
        header = evt.get("header", {})
        if header.get("event_type") == "im.message.receive_v1":
            # ACK to Feishu IMMEDIATELY — handle in background thread so
            # Feishu does not time out and retry this request.
            import threading
            threading.Thread(target=self._handle_message,
                              args=(evt.get("event", {}),),
                              daemon=True).start()
            self._send_json({"ok": True})
            return
        # V1 fallback.
        if evt.get("event") and evt["event"].get("type") == "message":
            import threading
            threading.Thread(target=self._handle_message_v1,
                              args=(evt["event"],),
                              daemon=True).start()
            self._send_json({"ok": True})
            return
        self._send_json({"ok": True, "ignored": True})

    def _handle_message(self, event: dict) -> None:
        from roborsi.channels.agent.feishu.feishu_extras import (
            is_allowed, add_reaction, remove_reaction, chat_lock,
        )
        from roborsi.channels.agent.feishu.feishu_integration import (
            handle_command, set_run_target_chat,
        )
        msg = event.get("message", {})
        if msg.get("message_type") != "text":
            return
        try:
            content = json.loads(msg.get("content", "{}"))
            text = content.get("text", "")
        except Exception:
            text = ""
        chat_id = msg.get("chat_id")
        msg_id = msg.get("message_id")
        sender_open_id = (event.get("sender", {}).get("sender_id", {})
                          .get("open_id"))
        if not chat_id or not text:
            return
        if not is_allowed(sender_open_id):
            print(f"[feishu-bot] denied open_id={sender_open_id} (not in allowlist)")
            return
        set_run_target_chat(chat_id, message_id=msg_id)
        reaction_id = add_reaction(msg_id, "ThinkingFace") if msg_id else None
        # Process serially per chat — never parallel for same chat_id.
        with chat_lock(chat_id):
            try:
                card = handle_command(text)
            except Exception as e:
                print(f"[feishu-bot] handle_command err: {type(e).__name__}: {e}")
                if msg_id and reaction_id:
                    remove_reaction(msg_id, reaction_id)
                if msg_id:
                    add_reaction(msg_id, "CrossMark")
                return
            if card:
                _send_card(chat_id, card)
            if msg_id and reaction_id:
                remove_reaction(msg_id, reaction_id)

    def _handle_message_v1(self, event: dict) -> None:
        from roborsi.channels.agent.feishu.feishu_extras import (
            is_allowed, chat_lock,
        )
        from roborsi.channels.agent.feishu.feishu_integration import (
            handle_command, set_run_target_chat,
        )
        text = event.get("text_without_at_bot") or event.get("text", "")
        chat_id = event.get("open_chat_id") or event.get("chat_id")
        sender_open_id = event.get("open_id")
        if not chat_id or not text:
            return
        if not is_allowed(sender_open_id):
            return
        set_run_target_chat(chat_id)
        with chat_lock(chat_id):
            card = handle_command(text)
            if card:
                _send_card(chat_id, card)

    def _send(self, status: int, body: str) -> None:
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def _send_json(self, obj) -> None:
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


def serve(port: int = 9876, host: str = "0.0.0.0") -> None:
    """Start the Feishu bot event server (foreground)."""
    httpd = ThreadingHTTPServer((host, port), _BotHandler)
    print(f"[feishu-bot] listening on http://{host}:{port}/feishu/event")
    print(f"[feishu-bot] env: APP_ID={'set' if os.environ.get('FEISHU_APP_ID') else 'MISSING'}, "
          f"APP_SECRET={'set' if os.environ.get('FEISHU_APP_SECRET') else 'MISSING'}, "
          f"VERIFICATION_TOKEN={'set' if os.environ.get('FEISHU_VERIFICATION_TOKEN') else 'MISSING'}")
    print(f"[feishu-bot] supported commands: /audit, /approve, /reject, /list, /help")
    print(f"[feishu-bot] Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[feishu-bot] stopped.")
