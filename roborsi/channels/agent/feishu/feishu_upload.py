"""Feishu file upload (mp4 demo) + task summary cards."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from .bot_server import _get_tenant_token, _send_card


def _public_base_url() -> str:
    """Where the HTML monitor is reachable (for clickable links in Feishu)."""
    return os.environ.get("ROBORSI_MONITOR_URL", "http://localhost:8770")


def upload_file_to_feishu(file_path: Path) -> str | None:
    """Upload a video/file via tenant token; returns file_key, or None."""
    token = _get_tenant_token()
    if not token or not file_path.exists():
        return None
    import uuid
    boundary = "----RH" + uuid.uuid4().hex
    file_data = file_path.read_bytes()
    fname = file_path.name
    file_type = "mp4" if file_path.suffix.lower() == ".mp4" else "stream"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_type\"\r\n\r\n{file_type}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_name\"\r\n\r\n{fname}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"duration\"\r\n\r\n7000\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
        f"Content-Type: video/mp4\r\n\r\n").encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/files",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
            if d.get("code") == 0:
                return d.get("data", {}).get("file_key")
            print(f"[feishu-upload] {d}")
            return None
    except Exception as e:  # noqa: BLE001
        print(f"[feishu-upload] {e}")
        return None


def _send_file(chat_id: str, file_key: str, msg_type: str = "file") -> bool:
    token = _get_tenant_token()
    if not token:
        return False
    body = json.dumps({
        "receive_id": chat_id,
        "msg_type": msg_type,
        "content": json.dumps({"file_key": file_key}),
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
            if d.get("code") != 0:
                print(f"[feishu-upload] send error: {d}")
                return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[feishu-upload] send fail: {e}")
        return False


def push_task_result_to_chat(chat_id: str, status: dict[str, Any],
                              video_path: Path | None = None) -> None:
    """Build a success/failure card + upload demo video. Posts to chat."""
    run_id = status.get("run_id", "?")
    task = status.get("task", "?")
    st = status.get("status", "?")
    outcome = status.get("outcome") or "?"
    summary = status.get("summary", "")
    n_tools = len((status.get("episode_summary") or {}).get("vlm_trace") or [])
    monitor_link = f"{_public_base_url()}/run/{run_id}"

    if st == "success":
        title = f"✓ {task} succeeded"
        template = "green"
    elif st == "failed":
        title = f"✗ {task} failed"
        template = "red"
    elif st == "error":
        title = f"⚠ {task} errored"
        template = "orange"
    else:
        title = f"{task} — {st}"
        template = "blue"

    card = {
        "header": {"title": {"tag": "plain_text", "content": title},
                   "template": template},
        "elements": [
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**run id**\n`{run_id}`"}},
                {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**seed**\n`{status.get('seed','?')}`"}},
                {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**outcome**\n`{outcome}`"}},
                {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**tool calls**\n`{n_tools}`"}},
            ]},
            {"tag": "div", "text": {"tag": "lark_md",
              "content": f"**summary**\n{summary}"}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🌐 Open Monitor"},
                 "type": "primary", "url": monitor_link},
            ]},
            {"tag": "note", "elements": [{"tag": "plain_text",
              "content": f"started {status.get('started','?')} · finished {status.get('finished','?')}"}]},
        ],
    }
    _send_card(chat_id, card)

    if video_path and video_path.exists():
        fk = upload_file_to_feishu(video_path)
        if fk:
            _send_file(chat_id, fk, msg_type="file")


def push_failure_alert(chat_id: str, status: dict[str, Any],
                        last_frame_path: Path | None = None) -> None:
    """Loud failure card — last frame attached as image if available."""
    run_id = status.get("run_id", "?")
    task = status.get("task", "?")
    summary = status.get("summary", "")
    monitor_link = f"{_public_base_url()}/run/{run_id}"
    log_tail = (status.get("log_tail", "") or "")[-400:]
    card = {
        "header": {"title": {"tag": "plain_text",
                              "content": f"⚠️ FAILURE: {task} [{run_id}]"},
                   "template": "red"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
              "content": f"**summary**: {summary}\n**outcome**: `{status.get('outcome','?')}`"}},
            {"tag": "div", "text": {"tag": "lark_md",
              "content": f"**log tail (last 400 chars)**\n```\n{log_tail}\n```"}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🌐 Investigate"},
                 "type": "danger", "url": monitor_link},
            ]},
        ],
    }
    _send_card(chat_id, card)
