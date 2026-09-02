"""Human-in-the-loop approval for VLM-authored base skills.

Used by base/robotwin/register_skill before activating a new VLM-defined
helper. Two channels:

  - interactive (TTY): print proposal → input('Approve [y/n]: ')
  - queue (subprocess): write JSON to ~/.roborsi/skill_review/, poll
    for human-flipped status

Env vars:
  ROBORSI_SKILL_AUTO_APPROVE=1  → bypass (batch e2e), logged
  ROBORSI_SKILL_REJECT_ALL=1    → reject all (safety override)
  ROBORSI_SKILL_REVIEW_TIMEOUT_S=300  → queue mode poll budget
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_QUEUE_DIR = Path.home() / ".roborsi" / "skill_review"


@dataclass
class ReviewVerdict:
    approved: bool
    mode: str             # "interactive" | "queue" | "auto_approve" | "reject_all"
    reviewer_note: str    # human's reason or auto-mode tag
    elapsed_s: float


def review_proposal(*, name: str, code: str, docstring: str,
                     test_call_args: dict | None = None,
                     task_name: str | None = None,
                     test_images: dict[str, str] | None = None,
                     test_result_preview: str | None = None,
                     ) -> ReviewVerdict:
    """Block until verdict (approve/reject/timeout). See module doc."""
    t0 = time.time()
    if os.environ.get("ROBORSI_SKILL_REJECT_ALL"):
        return ReviewVerdict(False, "reject_all",
                              "ROBORSI_SKILL_REJECT_ALL set",
                              time.time() - t0)
    if os.environ.get("ROBORSI_SKILL_AUTO_APPROVE"):
        # Still log the proposal for audit.
        _log_audit(name, code, docstring, task_name, "auto_approved",
                    test_images=test_images, test_result_preview=test_result_preview)
        return ReviewVerdict(True, "auto_approve",
                              "ROBORSI_SKILL_AUTO_APPROVE set",
                              time.time() - t0)
    if _is_interactive_tty():
        verdict = _interactive_prompt(name, code, docstring, test_call_args,
                                       test_images, test_result_preview)
    else:
        verdict = _queue_review(name, code, docstring, test_call_args, task_name,
                                 test_images, test_result_preview)
    verdict.elapsed_s = time.time() - t0
    _log_audit(name, code, docstring, task_name,
                "approved" if verdict.approved else "rejected",
                note=verdict.reviewer_note,
                test_images=test_images, test_result_preview=test_result_preview)
    return verdict


def _is_interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _interactive_prompt(name: str, code: str, docstring: str,
                         test_args, test_images=None,
                         test_result_preview=None) -> ReviewVerdict:
    print("\n" + "=" * 72, file=sys.stderr)
    print(f"VLM PROPOSES NEW BASE SKILL: {name}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"Docstring: {docstring}", file=sys.stderr)
    print("-" * 72, file=sys.stderr)
    print("Code:", file=sys.stderr)
    for line in code.splitlines():
        print(f"  {line}", file=sys.stderr)
    if test_args:
        print(f"Test args: {test_args}", file=sys.stderr)
    if test_result_preview:
        print(f"Test result: {test_result_preview[:200]}", file=sys.stderr)
    if test_images:
        print(f"Demo images:", file=sys.stderr)
        for k, v in test_images.items():
            if v: print(f"  {k}: {v}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    while True:
        ans = input("Approve this skill? [y]es/[n]o/[s]how again: ").strip().lower()
        if ans in ("y", "yes"):
            note = input("Optional comment: ").strip()
            return ReviewVerdict(True, "interactive", note, 0.0)
        if ans in ("n", "no"):
            note = input("Why reject (will surface to VLM): ").strip()
            return ReviewVerdict(False, "interactive", note, 0.0)
        if ans in ("s", "show"):
            print(code, file=sys.stderr)


def _queue_review(name: str, code: str, docstring: str, test_args,
                   task_name: str | None,
                   test_images: dict[str, str] | None = None,
                   test_result_preview: str | None = None) -> ReviewVerdict:
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    proposal_id = f"{int(time.time())}-{name}"
    p = _QUEUE_DIR / f"{proposal_id}.json"
    proposal_data = {
        "id": proposal_id,
        "name": name,
        "task_name": task_name,
        "docstring": docstring,
        "code": code,
        "test_call_args": test_args,
        "test_images": test_images or {},
        "test_result_preview": test_result_preview,
        "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",          # human flips to "approved" / "rejected"
        "reviewer_note": "",
    }
    p.write_text(json.dumps(proposal_data, indent=2))
    # Push notification to Feishu if configured (FEISHU_WEBHOOK_URL env).
    if os.environ.get("FEISHU_WEBHOOK_URL"):
        try:
            from roborsi.channels.agent.feishu.feishu_integration import (
                push_proposal_to_feishu,
            )
            push_proposal_to_feishu(proposal_data)
        except Exception as e:
            print(f"[skill review] feishu push failed: {e}")
    timeout_s = int(os.environ.get("ROBORSI_SKILL_REVIEW_TIMEOUT_S", 300))
    print(f"\n[skill review] proposal queued: {p}", file=sys.stderr)
    print(f"[skill review] approve via:  roborsi-sim skill approve {proposal_id}",
          file=sys.stderr)
    print(f"[skill review] reject via:   roborsi-sim skill reject {proposal_id} 'reason'",
          file=sys.stderr)
    print(f"[skill review] waiting up to {timeout_s}s ...", file=sys.stderr)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            time.sleep(2.0); continue
        status = data.get("status")
        if status == "approved":
            return ReviewVerdict(True, "queue",
                                  data.get("reviewer_note", ""), 0.0)
        if status == "rejected":
            return ReviewVerdict(False, "queue",
                                  data.get("reviewer_note", "rejected by human"),
                                  0.0)
        time.sleep(2.0)
    return ReviewVerdict(False, "queue",
                          f"approval timed out after {timeout_s}s", 0.0)


def _log_audit(name: str, code: str, docstring: str,
                task_name: str | None, verdict: str, note: str = "",
                test_images: dict | None = None,
                test_result_preview: str | None = None) -> None:
    audit_dir = Path.home() / ".roborsi" / "skill_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    p = audit_dir / f"{int(time.time())}-{name}-{verdict}.json"
    p.write_text(json.dumps({
        "name": name, "task_name": task_name,
        "docstring": docstring, "code": code,
        "verdict": verdict, "reviewer_note": note,
        "test_images": test_images or {},
        "test_result_preview": test_result_preview,
        "logged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2))


# ────────────────────────────────────────────────────────────────────────
# CLI helpers (used by `roborsi-sim skill approve|reject|list`)
# ────────────────────────────────────────────────────────────────────────


def list_pending() -> list[dict[str, Any]]:
    if not _QUEUE_DIR.exists():
        return []
    out = []
    for p in sorted(_QUEUE_DIR.iterdir()):
        if p.suffix != ".json": continue
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if d.get("status") == "pending":
            out.append(d)
    return out


def approve(proposal_id: str, note: str = "") -> bool:
    return _flip_status(proposal_id, "approved", note)


def reject(proposal_id: str, note: str = "") -> bool:
    return _flip_status(proposal_id, "rejected", note)


def _flip_status(proposal_id: str, new_status: str, note: str) -> bool:
    p = _QUEUE_DIR / f"{proposal_id}.json"
    if not p.exists():
        return False
    d = json.loads(p.read_text())
    d["status"] = new_status
    d["reviewer_note"] = note
    d["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(d, indent=2))
    return True
