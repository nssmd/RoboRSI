"""Feishu (Lark) integration for VLM-skill review.

Two channels:
  - PUSH (out): notify a Feishu group when a new proposal lands.
                Uses incoming webhook (https://open.feishu.cn).
                Env: FEISHU_WEBHOOK_URL.
  - BOT (in): receive Feishu event callbacks. Listens for /audit
              commands, runs skill_audit, replies as rich card.
              Env: FEISHU_VERIFICATION_TOKEN, FEISHU_ENCRYPT_KEY (opt),
                   FEISHU_APP_ID, FEISHU_APP_SECRET.

Both are optional — if env vars missing, the integration sits dormant.

Webhook payload format ref: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message-card
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error


# ────────────────────────────────────────────────────────────────────────
# PUSH: notify Feishu when new proposal lands
# ────────────────────────────────────────────────────────────────────────


def push_proposal_to_feishu(proposal: dict[str, Any], *,
                              review_ui_url: str | None = None,
                              webhook_url: str | None = None) -> bool:
    """POST a rich card to Feishu webhook announcing a new skill proposal.
    Returns True on success."""
    url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL")
    if not url:
        return False
    review_ui_url = review_ui_url or os.environ.get(
        "ROBORSI_SKILL_REVIEW_URL", "http://localhost:8765/")
    card = _build_proposal_card(proposal, review_ui_url)
    payload = {"msg_type": "interactive", "card": card}
    return _post_webhook(url, payload)


def push_audit_to_feishu(audit: dict[str, Any], proposal: dict[str, Any], *,
                           webhook_url: str | None = None) -> bool:
    """POST AI audit result to Feishu as a rich card."""
    url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL")
    if not url:
        return False
    card = _build_audit_card(audit, proposal)
    payload = {"msg_type": "interactive", "card": card}
    return _post_webhook(url, payload)


def _build_proposal_card(p: dict, review_url: str) -> dict:
    name = p.get("name", "?")
    task = p.get("task_name", "shared")
    doc = (p.get("docstring") or "")[:200]
    code = (p.get("code") or "")[:600]
    pid = p.get("id", "?")
    return {
        "header": {
            "title": {"tag": "plain_text",
                       "content": f"🛠 New skill proposal: {name}"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div",
             "fields": [
                 {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**Task**\n`{task}`"}},
                 {"is_short": True, "text": {"tag": "lark_md",
                  "content": f"**Proposal id**\n`{pid}`"}},
             ]},
            {"tag": "div", "text": {"tag": "lark_md",
              "content": f"**Docstring**\n{doc}"}},
            {"tag": "div", "text": {"tag": "lark_md",
              "content": f"**Code**\n```python\n{code}\n```"}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🌐 Open Review UI"},
                 "type": "primary",
                 "url": review_url},
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🤖 Audit (type in chat)"},
                 "type": "default",
                 "value": {"action": "audit", "id": pid}},
            ]},
            {"tag": "note", "elements": [{"tag": "plain_text",
              "content": (
                  f"Reply '/audit {pid}' or '/audit {name}' to run AI audit; "
                  f"'/approve {pid}' or '/reject {pid} reason' to decide.")}]},
        ],
    }


def _build_audit_card(audit: dict, p: dict) -> dict:
    verdict = (audit.get("verdict") or "REQUEST_CHANGES").upper()
    color_map = {"APPROVE": "green", "REQUEST_CHANGES": "orange",
                 "REJECT": "red"}
    template = color_map.get(verdict, "blue")
    name = p.get("name", "?")
    fixes = audit.get("specific_fixes") or []
    fixes_md = "\n".join(f"- {f}" for f in fixes) if fixes else "_none_"

    def _line(dim: str) -> str:
        d = audit.get(dim, {}) or {}
        score = d.get("score")
        score_str = "—" if score is None else f"**{score}/10**"
        concerns = d.get("concerns") or []
        c = " · ".join(concerns) if concerns else "_no concerns_"
        return f"{dim:<14} {score_str}  {c}"

    body_md = (
        f"**Summary**: {audit.get('summary', '')}\n\n"
        f"**Matches docstring**: {'✓' if audit.get('matches_docstring') else '✗'}\n\n"
        f"```\n{_line('safety')}\n{_line('correctness')}\n"
        f"{_line('robustness')}\n{_line('code_quality')}\n```\n\n"
        f"**Specific fixes**:\n{fixes_md}"
    )
    return {
        "header": {
            "title": {"tag": "plain_text",
                       "content": f"🤖 AI Audit: {name} → {verdict}"},
            "template": template,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body_md}},
            {"tag": "note", "elements": [{"tag": "plain_text",
              "content": f"audited at {audit.get('audited_at')} · "
                          f"model: {(audit.get('model') or '?').split('/')[-1]}"}]},
        ],
    }


def _post_webhook(url: str, payload: dict) -> bool:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
            return 200 <= r.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[feishu] webhook post failed: {e}")
        return False


# ────────────────────────────────────────────────────────────────────────
# BOT: receive Feishu events, handle /audit /approve /reject /list commands
# ────────────────────────────────────────────────────────────────────────


def handle_command(text: str) -> dict[str, Any] | None:
    """Parse a Feishu chat message → dispatch.

    Two paths:
      1. Strict `/cmd args` slash-commands (for skill review legacy: /audit,
         /approve, /reject, /list).
      2. Everything else → Claude Opus 4.7 free-form intent parser. Returns
         {intent, task?, run_id?} which we dispatch.
    """
    text = (text or "").strip()
    if not text:
        return None
    # Legacy strict slash commands kept for skill review compatibility.
    if text.startswith("/"):
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower().lstrip("/")
        arg = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""
        if cmd == "audit":   return _do_audit_cmd(arg)
        if cmd == "harness":
            from .feishu_review import do_harness_cmd
            return do_harness_cmd(arg)
        if cmd == "approve":
            from .feishu_review import approve_with_gate
            _ok, card = approve_with_gate(arg, rest)
            return card
        if cmd == "reject":
            from .feishu_review import reject_with_archive
            _ok, card = reject_with_archive(arg, rest or "rejected via Feishu")
            return card
        if cmd in ("list", "list_proposals"): return _do_list_cmd()
        if cmd == "help":    return _help_card()
        # Otherwise fall through to LLM intent parser.
    # LLM intent parsing for free-form messages.
    intent = _llm_parse_intent(text)
    return _dispatch_intent(intent, text)


def _llm_parse_intent(text: str) -> dict[str, Any]:
    """Ask Claude Opus 4.7 to classify the user's intent. Returns dict like:
       {"intent": "run_task" | "list_tasks" | "status" | "runs" | "help" | "ignore",
        "task": "<canonical task name>",  # if run_task
        "run_id": "<id>",                  # if status
        "comment": "<bot reply hint>"}"""
    task_catalog = "\n".join(
        f"  - {name}: {info['zh']}"
        for name, info in _TASK_CATALOG.items())
    system = (
        "You are an intent classifier for a robotics-task chat bot. "
        "User messages may be in Chinese or English. Classify into one of:\n"
        "  - run_task: user wants to trigger a robot task. Set `task` to the "
        "canonical name (must be in catalog).\n"
        "  - list_tasks: user wants to know what tasks are available, OR sent "
        "a greeting/small-talk (greeting → show available tasks as friendly intro).\n"
        "  - status: user wants status of a specific run (set `run_id`)\n"
        "  - runs: user wants list of recent runs\n"
        "  - help: user wants help / usage info\n"
        "  - ignore: spam / clearly unrelated\n\n"
        f"Available task catalog:\n{task_catalog}\n\n"
        "Reply with ONLY a JSON object, no prose, no markdown fences. "
        "Example: {\"intent\": \"run_task\", \"task\": \"click_bell\"}"
    )
    raw = ""
    try:
        from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
        from roborsi.embodied.agent_loop.vlm_io import _call_vlm_no_tools
        raw = _call_vlm_no_tools(DEFAULT_MODEL, [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ])
        print(f"  [llm raw] {raw[:200]!r}", flush=True)
    except Exception as e:
        print(f"  [llm err] {type(e).__name__}: {e}", flush=True)
        return {"intent": "list_tasks", "comment": f"llm call failed, falling back"}
    # Strip code fences if any.
    import re as _re
    m = _re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"intent": "list_tasks", "comment": f"unparseable: {raw[:120]!r}"}
    try:
        d = json.loads(m.group(0))
        if not isinstance(d, dict) or "intent" not in d:
            return {"intent": "list_tasks", "comment": f"bad shape: {raw[:120]!r}"}
        return d
    except json.JSONDecodeError as e:
        return {"intent": "list_tasks", "comment": f"json parse: {e}"}


def _dispatch_intent(intent_obj: dict[str, Any], original_text: str
                      ) -> dict[str, Any] | None:
    intent = intent_obj.get("intent", "ignore")
    if intent == "list_tasks":
        return _do_tasks_list_cmd()
    if intent == "run_task":
        task = (intent_obj.get("task") or "").strip()
        if task and task in _TASK_CATALOG:
            return _do_run_cmd(task, chat_id_hint=None)
        return _text_card(
            f"我理解你想跑任务，但识别不出 task 名 (LLM: {intent_obj.get('task')!r}). "
            f"试试 \"有什么任务\" 看可选项。")
    if intent == "status":
        return _do_status_cmd(intent_obj.get("run_id", ""))
    if intent == "runs":
        return _do_runs_list_cmd()
    if intent == "help":
        return _help_card()
    return None   # ignore (small talk)


# Task catalog: tested → confidence + Chinese description.
# Anything not listed here that exists in RoboTwin/envs/ is shown as
# "available but untested" so the user knows what they could try.
_TASK_CATALOG = {
    "click_bell": {
        "zh": "按响一个铃铛", "tested": True, "demo": True,
        "aliases": ["bell", "click_bell"]},
    "click_alarmclock": {
        "zh": "按响闹钟", "tested": False, "demo": False,
        "aliases": ["alarm", "alarmclock"]},
    "beat_block_hammer": {
        "zh": "用锤子敲方块（1g 物体物理 hack）",
        "tested": True, "demo": True, "needs_attach": True,
        "aliases": ["hammer", "bbh", "block"]},
    "grab_roller": {
        "zh": "双臂抓滚筒", "tested": False, "demo": False,
        "aliases": ["roller"]},
    "pick_dual_bottles": {
        "zh": "双臂同时拿两瓶子", "tested": False, "demo": False,
        "aliases": ["bottle", "bottles"]},
    "handover_block": {
        "zh": "把方块从一只手传到另一只", "tested": False, "demo": False,
        "aliases": ["handover"]},
    "lift_pot": {
        "zh": "双臂抬锅", "tested": False, "demo": False,
        "aliases": ["pot"]},
}


# _TASK_ALIASES kept (still used internally for compatibility, but free-form
# input now flows through the LLM intent parser).
_TASK_ALIASES = {a: name for name, info in _TASK_CATALOG.items()
                  for a in info["aliases"]}


def _do_tasks_list_cmd() -> dict[str, Any]:
    """Card listing tested + available tasks for the user to pick."""
    tested = []
    untested = []
    for name, info in _TASK_CATALOG.items():
        line = f"  • `{name}` — {info['zh']}"
        if info.get("demo"):
            line += " 🎬"
        if info.get("needs_attach"):
            line += " ⚠sim-hack"
        if info["tested"]:
            tested.append(line)
        else:
            untested.append(line)
    # Discover other RoboTwin envs not in catalog.
    extra = _discover_extra_tasks()
    extra_md = ""
    if extra:
        extra_md = ("\n\n**更多（未测试）**:\n  "
                    + ", ".join(f"`{n}`" for n in extra[:15]))
        if len(extra) > 15:
            extra_md += f" … 共 {len(extra)} 个"
    md = (
        "**✅ 已测过 (推荐)**\n" + ("\n".join(tested) if tested else "  (无)")
        + "\n\n**🧪 在 catalog 里但未充分测试**\n"
        + ("\n".join(untested) if untested else "  (无)")
        + extra_md
        + "\n\n**用法**：直接说 \"测 bell\" / \"测一下 hammer\" 我就跑。"
    )
    return {
        "header": {"title": {"tag": "plain_text",
                              "content": "🤖 仿真可做的任务"},
                   "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}}],
    }


def _discover_extra_tasks() -> list[str]:
    """List RoboTwin envs/*.py not in _TASK_CATALOG. Best-effort, never throws."""
    import os
    root = os.environ.get("ROBORSI_ROBOTWIN_ROOT") or str(
        Path.home() / "RoboTwin"
    )
    envs = Path(root) / "envs"
    if not envs.is_dir():
        return []
    cataloged = set(_TASK_CATALOG.keys())
    out = []
    for p in sorted(envs.iterdir()):
        if p.suffix != ".py" or p.stem.startswith("_"):
            continue
        if p.stem not in cataloged:
            out.append(p.stem)
    return out


_TASK_ALIASES = {a: name for name, info in _TASK_CATALOG.items()
                  for a in info["aliases"]}


def _do_run_cmd(arg: str, chat_id_hint: str | None) -> dict[str, Any]:
    """Spawn task subprocess. Returns immediate ack card with monitor link."""
    if not arg:
        avail = ", ".join(sorted(set(_TASK_ALIASES.values())))
        return _text_card(f"usage: /run <task>\navailable: {avail}\n"
                          f"or natural: '测一个 bell'")
    task = _TASK_ALIASES.get(arg.lower(), arg)
    import os
    from .task_runner import spawn_task, render_demo_video
    from .feishu_upload import (push_task_result_to_chat,
                                  push_failure_alert, _public_base_url)
    from roborsi.store import trace_db as _td

    # Store reply chat_id at module level so on_complete callback can push back.
    target_chat = _RUN_TARGET_CHAT.get("chat_id")

    def _on_complete(rid: str):
        st = _td.get_run(rid) or {}
        if not target_chat:
            print(f"[feishu] no target chat for {rid}; skipping result push")
            return
        video = render_demo_video(rid, camera="head_camera")
        push_task_result_to_chat(target_chat, st, video_path=video)
        if st.get("status") in ("failed", "error"):
            push_failure_alert(target_chat, st)

    run_id = spawn_task(task=task, seed=0, episodes=1,
                          on_complete=_on_complete)
    monitor = f"{_public_base_url()}/run/{run_id}"
    return {
        "header": {"title": {"tag": "plain_text",
                              "content": f"🚀 Task started: {task}"},
                   "template": "blue"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
              "content": (f"**run id**: `{run_id}`\n"
                           f"**task**: `{task}`\n"
                           f"**status page**: [{monitor}]({monitor})\n"
                           f"I'll push the result + demo video here when done.")}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "🌐 Open Monitor"},
                 "type": "primary", "url": monitor},
            ]},
        ],
    }


def _do_status_cmd(run_id: str) -> dict[str, Any]:
    from roborsi.store import trace_db as _td
    if not run_id:
        return _text_card("usage: /status <run_id>")
    st = _td.get_run(run_id)
    if not st:
        return _text_card(f"run {run_id} not found")
    return _text_card(
        f"**{run_id}** ({st.get('task')}) — `{st.get('status')}`\n"
        f"{st.get('summary','')}")


def _do_runs_list_cmd() -> dict[str, Any]:
    from roborsi.store import trace_db as _td
    runs = _td.list_runs(limit=10)
    if not runs:
        return _text_card("no runs yet")
    md = "**Recent runs**:\n" + "\n".join(
        f"- `{r.get('id')}` {r.get('task')} → {r.get('status')} "
        f"({r.get('outcome','')})" for r in runs)
    return {"elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}}]}


_RUN_TARGET_CHAT: dict[str, str] = {}


def set_run_target_chat(chat_id: str, message_id: str | None = None) -> None:
    """Bot server calls this when a task-trigger message arrives, so
    on_complete callback knows where to push the result.
    `message_id` (optional) lets us reply-in-thread instead of bulk push."""
    _RUN_TARGET_CHAT["chat_id"] = chat_id
    if message_id is not None:
        _RUN_TARGET_CHAT["message_id"] = message_id


def _do_audit_cmd(arg: str) -> dict:
    if not arg:
        return _text_card("usage: /audit <proposal_id_or_name>")
    p = _find_proposal(arg)
    if not p:
        return _text_card(f"proposal not found: {arg}")
    from roborsi.embodied.skills._lib.human_review.skill_audit import (
        audit_skill,
    )
    audit = audit_skill(
        name=p.get("name", "?"),
        code=p.get("code", ""),
        docstring=p.get("docstring", ""),
        task_name=p.get("task_name", "shared"),
        test_result_preview=p.get("test_result_preview"),
        test_image_paths=p.get("test_images"),
    )
    # Persist back into queue file too.
    pid = p.get("id")
    if pid:
        from pathlib import Path as _P
        qpath = _P.home() / ".roborsi" / "skill_review" / f"{pid}.json"
        if qpath.exists():
            data = json.loads(qpath.read_text())
            data["audit"] = audit
            qpath.write_text(json.dumps(data, indent=2))
    return _build_audit_card(audit, p)


def _do_decide_cmd(action: str, pid: str, note: str) -> dict:
    if not pid:
        return _text_card(f"usage: /{action} <proposal_id> [note]")
    from roborsi.embodied.skills._lib.human_review.skill_review import (
        approve, reject,
    )
    ok = approve(pid, note) if action == "approve" else reject(pid, note)
    icon = "✓" if action == "approve" else "✗"
    template = "green" if action == "approve" else "red"
    msg = (f"{icon} {action}d {pid}" + (f"\n_{note}_" if note else "")
           if ok else f"proposal not found: {pid}")
    return {
        "header": {"title": {"tag": "plain_text",
                              "content": f"{icon} {action.title()} proposal"},
                   "template": template},
        "elements": [{"tag": "div", "text": {"tag": "lark_md",
                                                "content": msg}}],
    }


def _do_list_cmd() -> dict:
    from roborsi.embodied.skills._lib.human_review.skill_review import (
        list_pending,
    )
    pend = list_pending()
    if not pend:
        return _text_card("no pending proposals")
    rows = []
    for p in pend[:10]:
        rows.append(f"- `{p.get('id')}` · {p.get('name')} ({p.get('task_name')}) — "
                    f"{(p.get('docstring') or '')[:60]}")
    md = "**Pending proposals**\n" + "\n".join(rows)
    return {
        "header": {"title": {"tag": "plain_text",
                              "content": f"📋 {len(pend)} pending"},
                   "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md",
                                                "content": md}}],
    }


def _find_proposal(arg: str) -> dict[str, Any] | None:
    """Match by id (exact) or by name (suffix match)."""
    qd = Path.home() / ".roborsi" / "skill_review"
    if not qd.exists():
        return None
    # Try exact id first.
    p = qd / f"{arg}.json"
    if p.exists():
        return json.loads(p.read_text())
    # Match by name within pending.
    for jf in sorted(qd.glob("*.json")):
        try:
            d = json.loads(jf.read_text())
        except Exception:
            continue
        if d.get("name") == arg or d.get("id", "").endswith(arg):
            return d
    return None


def _text_card(text: str) -> dict:
    return {
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }


def _help_card() -> dict:
    return {
        "header": {"title": {"tag": "plain_text",
                              "content": "RoboRSI Bot — commands"},
                   "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": (
            "**🤖 Run robot tasks:**\n"
            "**问任务清单** — \"有什么任务\" / \"能做啥\" / `/tasks`\n"
            "**触发任务** — \"测一个 bell\" / \"试一下 hammer\" / `/run <task>`\n"
            "**查看状态** — `/status <run_id>` / `/runs`\n\n"
            "**🔍 Review VLM-proposed skills:**\n"
            "**/list** — list pending proposals\n"
            "**/audit** `<id|name>` — run AI audit on a proposed skill\n"
            "**/harness** `<skill>` — run the sim harness (no apply); fails fast\n"
            "**/approve** `<id> [note]` — gate (harness if base/robotwin) → apply + commit\n"
            "**/reject** `<id> reason` — archive without applying\n"
            "**/help** — this message"
        )}}],
    }
