"""Proposal harness gate — auto-mode validator for self-evolution proposals.

Used when filter_mode=auto. Single check:

  HARNESS (accuracy / correctness):
   - Stage the proposed new_code into the target skill's policy.py
   - Run scripts_lib_harness_gate.run_gate_for(skill_name)
   - Restore original on success or failure (no permanent write here)
   - Pass iff verdict == PASS

The SIMILARITY (duplicate-detection) check is intentionally NOT here —
per user 2026-06-10: similarity is judged by Claude (the human-in-the-loop
operator) when reviewing the queue, not by a separate LLM. The skill
index + proposal name/description are short enough to inspect directly.

This module is the "auto" filter for the *accuracy* dimension only.
Approval = Claude reads harness report + does similarity by eye, then
calls scripts/apply_selfevo_proposal.py to commit.

The "human" filter uses html_review.py / feishu cards to surface the
same data via approval UI.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_REPO = Path(__file__).resolve().parents[2]


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    detail: str = ""
    elapsed_s: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    proposal_id: str
    proposal_name: str
    overall_pass: bool
    harness: CheckOutcome | None = None
    # similarity intentionally absent — Claude judges by reading the proposal.
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stage_and_gate(name: str, new_code: str, skill_md: str = "") -> CheckOutcome:
    """Write new_code (and optional skill_md) into the skill's path, run
    harness gate, restore original. Safe: filesystem state is identical
    before and after this call regardless of verdict."""
    sys.path.insert(0, str(_REPO / "scripts"))
    from scripts_lib_harness_gate import run_gate_for

    t0 = time.time()
    skill_dir = _REPO / "roborsi" / "embodied" / "skills" / "base" / "robotwin" / name
    is_new_skill = not skill_dir.exists()
    pol_path = skill_dir / "policy.py"
    md_path = skill_dir / "SKILL.md"
    backups: dict[Path, bytes | None] = {}

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        for path, content in [(pol_path, new_code), (md_path, skill_md)]:
            if not content:
                continue
            backups[path] = path.read_bytes() if path.exists() else None
            path.write_text(content, encoding="utf-8")

        gate = run_gate_for(name, timeout_s=300)
        return CheckOutcome(
            name="harness",
            passed=(gate.verdict == "PASS"),
            detail=(f"verdict={gate.verdict} "
                    f"({gate.pass_count}/{gate.total}) {gate.reason}"[:400]),
            elapsed_s=round(time.time() - t0, 2),
            extras={"verdict": gate.verdict,
                    "stdout_tail": gate.stdout_tail[-300:]},
        )
    finally:
        for path, original in backups.items():
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)
        if is_new_skill and skill_dir.exists():
            try:
                skill_dir.rmdir()
            except OSError:
                pass


class ProposalValidator:
    """Auto-mode harness runner. Runs the per-skill harness against a
    staged proposal and returns a structured ValidationReport. No LLM
    calls — similarity is judged by Claude reading the queue."""

    def __init__(self, **_ignored: Any) -> None:
        # Accept legacy kwargs (model, similarity_threshold) so existing
        # callers don't break; we ignore them — there is no LLM here.
        pass

    def validate(self, proposal: dict[str, Any]) -> ValidationReport:
        name = proposal.get("name", "?")
        pid = proposal.get("id", f"unknown-{int(time.time())}")
        new_code = proposal.get("new_code") or proposal.get("code") or ""
        skill_md = proposal.get("skill_md") or ""
        rep = ValidationReport(
            proposal_id=pid, proposal_name=name, overall_pass=False,
        )
        if not new_code:
            rep.harness = CheckOutcome(
                name="harness", passed=False,
                detail="proposal has empty new_code — nothing to validate",
            )
            rep.note = ("BLOCKED — no new_code in proposal. "
                        "Claude must reject or request Reviewer to refile.")
            return rep
        rep.harness = _stage_and_gate(name, new_code, skill_md)
        rep.overall_pass = rep.harness.passed
        if rep.overall_pass:
            rep.note = ("HARNESS PASSED — proposal eligible for Claude "
                         "similarity review + apply.")
        else:
            rep.note = ("BLOCKED at harness check — proposal failed real-sim "
                         "validation. Claude should reject or ask Reviewer to "
                         "refine.")
        return rep
