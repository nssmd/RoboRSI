"""Per-task archive of successful plan.md files.

When Engineer.execute reports success, the plan.md that produced it
gets archived to `roborsi/embodied/skills/_data/successful_plans/<task>.jsonl`
along with the commit shas of the base skills it invoked.

This is REAL learned knowledge — same status as skill code itself —
so it lives IN the git repo, not under ~/.roborsi (which is a
runtime cache). gitignore the file if you want ephemeral behavior on
a particular dev box.

Each record:
  {ts, plan, used_skills: {skill_name: commit_sha}}

Planner.plan() reads the last N records for the same task and prepends
them to its user context so it doesn't have to reinvent a working plan
from scratch.

Records expire the same way skill_history does: when any used skill's
policy.py gets a new commit, that record becomes stale (its stored sha
no longer matches HEAD). `get_recent_plans` silently drops stale ones.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from roborsi.agents.skill_history import _current_commit_sha, _REPO


_ARCHIVE_DIR = _REPO / "roborsi" / "embodied" / "skills" / "_data" / "successful_plans"


def _archive_path(task: str) -> Path:
    return _ARCHIVE_DIR / f"{task}.jsonl"


def archive_successful_plan(task: str, plan_md: str,
                              skills_used: list[str]) -> None:
    """Append one record per successful attempt. Skipped if plan empty."""
    if not plan_md.strip():
        return
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    used_skill_shas: dict[str, str] = {}
    for skill in skills_used:
        sha = _current_commit_sha(skill)
        if sha:
            used_skill_shas[skill] = sha
    record = {
        "ts": int(time.time()),
        "task": task,
        "plan": plan_md,
        "used_skills": used_skill_shas,
    }
    with _archive_path(task).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_recent_plans(task: str, n: int = 3) -> list[dict]:
    """Return the latest N records whose used_skills all still match
    current HEAD shas (i.e. the plan is not stale because no skill it
    used has been updated since)."""
    path = _archive_path(task)
    if not path.exists():
        return []
    fresh: list[dict] = []
    sha_cache: dict[str, str | None] = {}
    # Walk newest-to-oldest.
    with path.open(encoding="utf-8") as f:
        lines = f.readlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        used = rec.get("used_skills") or {}
        all_fresh = True
        for skill, stored_sha in used.items():
            if skill not in sha_cache:
                sha_cache[skill] = _current_commit_sha(skill)
            if sha_cache[skill] != stored_sha:
                all_fresh = False
                break
        if all_fresh:
            fresh.append(rec)
        if len(fresh) >= n:
            break
    return fresh


def format_for_planner(records: list[dict]) -> str:
    """Render records as a markdown block to prepend to Planner context."""
    if not records:
        return ""
    parts = ["=== PRIOR SUCCESSFUL PLANS FOR THIS TASK ===",
              "(filtered: skills these plans relied on all still HEAD)",
              ""]
    for i, rec in enumerate(records, 1):
        used = ", ".join(rec.get("used_skills", {})) or "(none recorded)"
        parts.append(f"## Prior plan #{i}  ·  used: {used}")
        parts.append(rec.get("plan", "(empty)"))
        parts.append("")
    return "\n".join(parts)
