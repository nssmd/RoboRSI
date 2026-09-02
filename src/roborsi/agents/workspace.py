"""Filesystem artifacts for one role-orchestrated episode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    task: str
    run_id: str
    root: Path

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.md"

    @property
    def plan_json_path(self) -> Path:
        return self.root / "plan.json"

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

    def write_plan_json(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.plan_json_path.write_text(
            json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def read_plan_json(self) -> dict[str, Any]:
        if not self.plan_json_path.is_file():
            return {}
        value = json.loads(self.plan_json_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def write_summary(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(text, encoding="utf-8")

    def read_summary(self) -> str:
        return self.summary_path.read_text(encoding="utf-8") if self.summary_path.is_file() else ""

    def write_review(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.review_path.write_text(text, encoding="utf-8")
