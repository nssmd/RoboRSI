"""Filesystem artifacts for one role-orchestrated episode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
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

    def write_plan(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(text, encoding="utf-8")

    def read_plan(self) -> str:
        return self.plan_path.read_text(encoding="utf-8") if self.plan_path.is_file() else ""

    def write_summary(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(text, encoding="utf-8")

    def read_summary(self) -> str:
        return self.summary_path.read_text(encoding="utf-8") if self.summary_path.is_file() else ""

    def write_review(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.review_path.write_text(text, encoding="utf-8")
