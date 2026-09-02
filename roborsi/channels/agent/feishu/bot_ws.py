"""Feishu WebSocket long-connection bot — NO incoming port needed.

Instead of running an HTTP server and asking
Feishu to push events to us via webhook (which needs a public URL,
tunnel, etc), we DIAL OUT to Feishu's servers and receive events on a
persistent WebSocket. Zero port config.

Required env:
  FEISHU_APP_ID, FEISHU_APP_SECRET

Optional:
  FEISHU_ALLOWED_USERS   — comma-separated open_ids; empty = allow everyone
  FEISHU_REACTIONS       — "false" to disable 🤔/❌ reactions
  ROBORSI_MONITOR_URL — used in card buttons (default http://localhost:8770)

Run:
  roborsi-sim skill feishu-bot-ws

Stop: Ctrl+C.
"""
from __future__ import annotations

import json
import os
import sys
import threading


def serve() -> None:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        sys.stderr.write("[feishu-ws] FEISHU_APP_ID / FEISHU_APP_SECRET required\n")
        sys.exit(1)

    # Start the monitor HTTP server in a thread of THIS same process, so
    # live_trace.{_SESSIONS,events} are shared in-memory between the
    # WebSocket handler and the HTTP handler. (Cross-process can't share
    # this without a DB; same-process via threads is the simple right answer.)
    from .status_server import serve as monitor_serve
    monitor_port = int(os.environ.get("ROBORSI_MONITOR_PORT", "8770"))
    threading.Thread(target=monitor_serve,
                       kwargs={"port": monitor_port, "host": "0.0.0.0"},
                       daemon=True, name="monitor-http").start()
    print(f"[feishu-ws] monitor HTTP server in same process → http://localhost:{monitor_port}/", flush=True)

    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    except ImportError as e:
        sys.stderr.write(f"[feishu-ws] lark_oapi not installed: {e}\n"
                          "  conda run -n RoboTwin pip install lark-oapi\n")
        sys.exit(1)

    def _on_message(data: P2ImMessageReceiveV1) -> None:
        """Inbound message handler. Runs in lark_oapi's worker thread."""
        print(f"[feishu-ws] RECEIVED message event", flush=True)
        from roborsi.channels.agent.feishu.feishu_extras import (
            is_duplicate_event, is_allowed, add_reaction, remove_reaction,
            chat_lock,
        )
        from roborsi.channels.core.agent import (
            handle_user_message,
        )
        from roborsi.channels.agent.feishu.bot_server import (
            _send_card, _send_text,
        )
        evt_id = getattr(data.header, "event_id", None)
        if is_duplicate_event(evt_id):
            print(f"  [skip dup] {evt_id}", flush=True)
            return
        msg = data.event.message
        print(f"  type={msg.message_type} chat_type={msg.chat_type} chat_id={msg.chat_id} msg_id={msg.message_id}", flush=True)
        if msg.message_type != "text":
            print(f"  [skip non-text]", flush=True)
            return
        try:
            content = json.loads(msg.content)
            text = content.get("text", "")
        except Exception:
            text = ""
        chat_id = msg.chat_id
        msg_id = msg.message_id
        sender_open_id = (data.event.sender.sender_id.open_id
                          if data.event.sender else None)
        print(f"  sender={sender_open_id} text={text!r}", flush=True)
        if not chat_id or not text:
            return
        if not is_allowed(sender_open_id):
            print(f"  [denied] not in allowlist", flush=True)
            return
        reaction_id = add_reaction(msg_id, "ThinkingFace") if msg_id else None
        # Run the agent in a worker thread so the lark_oapi WebSocket
        # heartbeat thread isn't blocked by the 30-90s sim execution.
        def _work():
            with chat_lock(chat_id):
                try:
                    print(f"  → opus agent loop…", flush=True)
                    reply = handle_user_message(text, target_chat_id=chat_id)
                    print(f"  ← reply: {reply[:200]!r}", flush=True)
                except Exception as e:
                    import traceback
                    print(f"  [ERR] {type(e).__name__}: {e}", flush=True)
                    traceback.print_exc()
                    if msg_id and reaction_id:
                        remove_reaction(msg_id, reaction_id)
                    if msg_id:
                        add_reaction(msg_id, "CrossMark")
                    _send_card(chat_id, {"elements": [{"tag": "div", "text": {
                        "tag": "lark_md", "content": f"⚠️ Error: {type(e).__name__}: {e}"}}]})
                    return
                _send_text(chat_id, reply)
                if msg_id and reaction_id:
                    remove_reaction(msg_id, reaction_id)
        import threading
        threading.Thread(target=_work, daemon=True,
                          name=f"agent-{msg_id[-8:] if msg_id else '?'}").start()

    event_handler = (lark.EventDispatcherHandler.builder("", "")
                      .register_p2_im_message_receive_v1(_on_message)
                      # Silence Lark "processor not found" noise for events we
                      # don't care about (reactions, message-read receipts).
                      .register_p2_im_message_reaction_created_v1(lambda d: None)
                      .register_p2_im_message_reaction_deleted_v1(lambda d: None)
                      .register_p2_im_message_message_read_v1(lambda d: None)
                      .build())
    cli = lark.ws.Client(
        app_id, app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,    # show connection status
    )
    print(f"[feishu-ws] connecting to Feishu (long connection)…", flush=True)
    print(f"[feishu-ws] app_id={app_id} reactions={os.environ.get('FEISHU_REACTIONS', 'on')}", flush=True)
    print(f"[feishu-ws] allowlist={os.environ.get('FEISHU_ALLOWED_USERS') or 'EVERYONE'}", flush=True)
    try:
        cli.start()
    except KeyboardInterrupt:
        print("\n[feishu-ws] stopped.")
