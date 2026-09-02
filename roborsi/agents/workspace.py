"""Per-task workspace dir + plan/summary/review document helpers.

Layout:
    ~/.roborsi/workspaces/<task>-<YYYYMMDD-HHMMSS>-<hex6>/
    ├── plan.md        # Planner authors, Engineer may amend
    ├── summary.md     # Engineer authors at end
    ├── review.md      # Reviewer authors
    └── proposal_id    # text file with skill_review/<pid> (if proposed)

This module owns the filesystem layout only. LLM calls happen in
planner.py / engineer.py / reviewer.py.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


_ROOT = Path.home() / ".roborsi" / "workspaces"


@dataclass(frozen=True)
class Workspace:
    """Filesystem handle for one atomic task attempt."""
    task: str
    run_id: str
    root: Path

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.md"

    @property
    def summary_path(self) -> Path:
        return self.root / "summary.md"

    @property
    def review_path(self) -> Path:
        return self.root / "review.md"

    @property
    def proposal_link_path(self) -> Path:
        return self.root / "proposal_id"

    def write_plan(self, text: str) -> None:
        self.plan_path.write_text(text, encoding="utf-8")

    def append_plan_note(self, note: str) -> None:
        """Engineer appends a short progress note. Preserves Planner body."""
        if not self.plan_path.exists():
            self.plan_path.write_text("", encoding="utf-8")
        with self.plan_path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n<!-- engineer note {time.strftime('%H:%M:%S')} -->\n{note}\n")

    def read_plan(self) -> str:
        return self.plan_path.read_text(encoding="utf-8") if self.plan_path.exists() else ""

    def write_summary(self, text: str) -> None:
        self.summary_path.write_text(text, encoding="utf-8")

    def read_summary(self) -> str:
        return self.summary_path.read_text(encoding="utf-8") if self.summary_path.exists() else ""

    def write_review(self, text: str) -> None:
        self.review_path.write_text(text, encoding="utf-8")

    def link_proposal(self, proposal_id: str) -> None:
        """Record the skill_review proposal id this workspace produced."""
        self.proposal_link_path.write_text(proposal_id, encoding="utf-8")


def new_workspace(task: str) -> Workspace:
    """Allocate a fresh workspace dir for this atomic attempt."""
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    root = _ROOT / f"{task}-{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    return Workspace(task=task, run_id=run_id, root=root)
