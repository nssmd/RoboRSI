"""Feishu human-in-loop review of base-skill proposals.

Two outbound helpers + the slash-command handlers that get wired into
feishu_integration.handle_command:

  notify_pending(chat_id)
    Scan ~/.roborsi/skill_review/*.json. For each pending whose pid
    is not already in /tmp/agent_loop/feishu_notified.json, push a card
    to chat_id with: name, kind, code_len, rationale (first 200 chars),
    harness verdict if base/robotwin and harness_args present, and the
    `/approve <pid>` + `/reject <pid>` hints. Append pid to notified set
    so duplicates are suppressed.

  run_harness_for_skill(skill_name) → dict
    Subprocess-runs scripts/test_base_skill.py <skill> --from-frontmatter.
    Returns the parsed verdict dict (per harness_standard).

  approve_with_gate(pid, note) → (ok: bool, card: dict)
    Per review_selfevo_proposal RULE 4a: if proposal targets a
    base/robotwin/*/policy.py, run harness first. Only apply if
    harness PASS. Otherwise return a REJECT card with the failure
    detail. Replaces the old /approve "just flip status" semantics.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


_QUEUE_DIR = Path.home() / ".roborsi" / "skill_review"
_REPO = Path(__file__).resolve().parents[3]
_NOTIFIED_FILE = Path("/tmp/agent_loop/feishu_notified.json")


def _load_notified() -> set[str]:
    if not _NOTIFIED_FILE.exists():
        return set()
    return set(json.loads(_NOTIFIED_FILE.read_text(encoding="utf-8")))


def _save_notified(s: set[str]) -> None:
    _NOTIFIED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTIFIED_FILE.write_text(json.dumps(sorted(s)), encoding="utf-8")


def _pending_proposals() -> list[dict]:
    if not _QUEUE_DIR.exists():
        return []
    out: list[dict] = []
    for fp in sorted(_QUEUE_DIR.glob("*.json")):
        d = json.loads(fp.read_text(encoding="utf-8"))
        if (d.get("status") or "pending") == "pending":
            out.append(d)
    return out


def _is_base_skill_proposal(d: dict) -> tuple[bool, str]:
    """Return (is_base, skill_name). Looks at category for kind=new, at
    target skill name for kind=update."""
    if d.get("kind") == "new":
        cat = str(d.get("category") or "")
        if cat.startswith("base/"):
            return True, str(d.get("name") or "")
    elif d.get("kind") == "update":
        nm = str(d.get("name") or "")
        skill_dir = (_REPO / "roborsi/embodied/skills/base" /
                       nm.split(".")[0])
        if skill_dir.exists():
            return True, nm.split(".")[0]
    return False, ""


def run_harness_for_skill(skill_name: str, timeout_s: int = 600) -> dict:
    """Drive scripts/test_base_skill.py --from-frontmatter via the shared
    harness-gate helper. Returns the gate result as a dict for card
    rendering."""
    import sys as _sys
    _sys.path.insert(0, str(_REPO / "scripts"))
    from scripts_lib_harness_gate import run_gate_for
    gr = run_gate_for(skill_name, timeout_s=timeout_s)
    return {"verdict": gr.verdict, "pass_count": gr.pass_count,
             "total": gr.total, "reason": gr.reason,
             "stdout_tail": gr.stdout_tail, "stderr_tail": gr.stderr_tail}


def approve_with_gate(pid: str, note: str = "") -> tuple[bool, dict]:
    """Apply a proposal — run harness first if it's a base/robotwin change."""
    fp = _QUEUE_DIR / f"{pid}.json"
    if not fp.exists():
        return False, _text_card(f"proposal not found: {pid}")
    d = json.loads(fp.read_text(encoding="utf-8"))
    is_base, skill_name = _is_base_skill_proposal(d)
    harness_info: dict | None = None
    if is_base:
        harness_info = run_harness_for_skill(skill_name)
        v = harness_info["verdict"]
        # SKIP is no longer auto-passed: a base/robotwin skill without a
        # harness: block is a governance failure. Operator must REJECT or
        # add the block before approve.
        if v != "PASS":
            return False, _harness_fail_card(pid, d, harness_info)
    # Run apply_selfevo_proposal.py (handles git commit + moving to applied/).
    cmd = ["python3", str(_REPO / "scripts/apply_selfevo_proposal.py"), pid]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                            cwd=str(_REPO), encoding="utf-8", errors="replace")
    ok = res.returncode == 0
    return ok, _apply_result_card(pid, d, ok, harness_info,
                                     stdout=res.stdout, stderr=res.stderr,
                                     note=note)


def reject_with_archive(pid: str, note: str = "") -> tuple[bool, dict]:
    cmd = ["python3", str(_REPO / "scripts/apply_selfevo_proposal.py"),
           "--reject", pid]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                            cwd=str(_REPO), encoding="utf-8", errors="replace")
    ok = res.returncode == 0
    return ok, _text_card(f"{'✗' if ok else '?'} {pid}: rejected "
                            f"({note or 'no reason given'})")


def notify_pending(chat_id: str) -> int:
    """Push one card per still-un-notified pending proposal. Returns count
    pushed."""
    from .bot_server import _send_card
    notified = _load_notified()
    pushed = 0
    for d in _pending_proposals():
        pid = d.get("id")
        if not pid or pid in notified:
            continue
        is_base, skill_name = _is_base_skill_proposal(d)
        card = _pending_card(d, is_base, skill_name)
        if _send_card(chat_id, card):
            notified.add(pid)
            pushed += 1
    if pushed:
        _save_notified(notified)
    return pushed


def _text_card(text: str) -> dict:
    return {"elements": [{"tag": "div",
                            "text": {"tag": "lark_md", "content": text}}]}


def _pending_card(d: dict, is_base: bool, skill_name: str) -> dict:
    pid = d.get("id", "?")
    code_len = len(d.get("new_code") or d.get("code") or "")
    rat = (d.get("rationale") or "")[:280]
    body = (f"**name**: `{d.get('name')}`  **kind**: `{d.get('kind')}`"
            f"  **code_len**: {code_len}\n"
            f"**rationale**: {rat}\n\n"
            f"**review hints**:\n"
            f"  • `/approve {pid}` — gate then apply\n"
            f"  • `/reject {pid} <reason>` — archive without apply\n"
            f"  • `/harness {skill_name}` — manual harness check\n")
    if is_base:
        body += ("\n⚠️ this is a **base skill change** — `/approve` will run "
                  "the harness gate first (per review_selfevo_proposal Rule 4a)")
    return {"header": {"title": {"tag": "plain_text",
                                     "content": f"🔍 pending review · {pid}"},
                           "template": "blue"},
             "elements": [{"tag": "div",
                            "text": {"tag": "lark_md", "content": body}}]}


def _harness_fail_card(pid: str, d: dict, hi: dict) -> dict:
    body = (f"**Harness BLOCKED** — proposal `{pid}` cannot be applied.\n"
            f"verdict: `{hi.get('verdict')}` ({hi.get('pass_count')}/{hi.get('total')})\n"
            f"reason: {hi.get('reason','')[:200]}\n\n"
            f"Fix the base skill until `--from-frontmatter` returns PASS, "
            f"then re-`/approve {pid}`.\n"
            f"Or `/reject {pid} <reason>` to discard.")
    return {"header": {"title": {"tag": "plain_text",
                                     "content": "✗ harness gate failed"},
                           "template": "red"},
             "elements": [{"tag": "div",
                            "text": {"tag": "lark_md", "content": body}}]}


def _apply_result_card(pid: str, d: dict, ok: bool, hi: dict | None,
                          stdout: str, stderr: str, note: str) -> dict:
    icon = "✓" if ok else "✗"
    template = "green" if ok else "red"
    head = f"{icon} apply {d.get('name')}"
    body_lines = [f"**proposal**: `{pid}`"]
    if hi:
        body_lines.append(f"**harness**: {hi.get('verdict')} "
                            f"({hi.get('pass_count')}/{hi.get('total')})")
    body_lines.append(f"**apply rc**: {0 if ok else 1}")
    tail = (stdout or stderr or "").strip().splitlines()[-3:]
    if tail:
        body_lines.append("```\n" + "\n".join(tail) + "\n```")
    if note:
        body_lines.append(f"_note_: {note}")
    return {"header": {"title": {"tag": "plain_text", "content": head},
                           "template": template},
             "elements": [{"tag": "div",
                            "text": {"tag": "lark_md",
                                       "content": "\n".join(body_lines)}}]}


# ─────────────────────── slash-command entry points ─────────────────────


def do_harness_cmd(skill_name: str) -> dict:
    if not skill_name:
        return _text_card("usage: /harness <skill_name>")
    hi = run_harness_for_skill(skill_name)
    icon = "✓" if hi["verdict"] == "PASS" else (
        "↷" if hi["verdict"] == "SKIP" else "✗")
    template = ("green" if hi["verdict"] == "PASS"
                  else "grey" if hi["verdict"] == "SKIP"
                  else "red")
    body = (f"**skill**: `{skill_name}`\n"
            f"**verdict**: `{hi['verdict']}` "
            f"({hi.get('pass_count','?')}/{hi.get('total','?')})\n"
            f"reason: {hi.get('reason','')[:250]}\n\n"
            f"```\n{(hi.get('stdout_tail') or '')[-400:]}\n```")
    return {"header": {"title": {"tag": "plain_text",
                                     "content": f"{icon} harness {skill_name}"},
                           "template": template},
             "elements": [{"tag": "div",
                            "text": {"tag": "lark_md", "content": body}}]}
