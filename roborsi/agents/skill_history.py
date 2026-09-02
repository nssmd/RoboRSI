"""Per-(task, skill, commit) success history for SkillSelector ranking.

Each successful atomic attempt appends a record to
`roborsi/embodied/skills/_data/skill_success.jsonl`. SkillSelector
queries this to bias its top-K ranking toward skills that have proven
to work on this task under their CURRENT code version.

This is REAL learned knowledge — same status as skill code itself — so
it lives IN the git repo. The old location (`~/.roborsi/...`) was
ephemeral cache; gitignore the in-repo file on a dev box if you want
to opt out of persistence locally.

Commit-sha gating means: when a base/robotwin/<name>/policy.py is
updated, the old success records become inert automatically — the
new attempts log a new sha; get_success_counts only counts records
whose stored sha matches the current HEAD sha for that skill's
policy.py. No manual reset needed.
"""
from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HISTORY = _REPO / "roborsi" / "embodied" / "skills" / "_data" / "skill_success.jsonl"


def _current_commit_sha(skill_name: str) -> str | None:
    """Return short HEAD sha that last touched base/robotwin/<skill>/policy.py.
    Returns None if the skill or git history is missing."""
    rel = f"roborsi/embodied/skills/base/{skill_name}/robotwin/policy.py"
    if not (_REPO / rel).exists():
        return None
    res = subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", rel],
        capture_output=True, text=True, cwd=_REPO, timeout=5,
    )
    sha = res.stdout.strip()
    return sha or None


def record_success(task: str, skills_used: list[str]) -> None:
    """Append one record per skill that fired in this success trace."""
    if not skills_used:
        return
    _HISTORY.parent.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    with _HISTORY.open("a", encoding="utf-8") as f:
        for skill in skills_used:
            sha = _current_commit_sha(skill)
            if sha is None:
                continue
            f.write(json.dumps({
                "ts": ts, "task": task, "skill": skill, "sha": sha,
            }) + "\n")


def get_success_counts(task: str) -> dict[str, int]:
    """For the given task, return {skill_name: success_count_under_current_sha}.
    Records whose stored sha != current sha are silently ignored —
    that's the auto-reset behavior."""
    if not _HISTORY.exists():
        return {}
    sha_cache: dict[str, str | None] = {}
    counts: Counter[str] = Counter()
    with _HISTORY.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("task") != task:
                continue
            name = rec.get("skill")
            stored_sha = rec.get("sha")
            if not name or not stored_sha:
                continue
            if name not in sha_cache:
                sha_cache[name] = _current_commit_sha(name)
            if sha_cache[name] is None:
                continue
            if stored_sha == sha_cache[name]:
                counts[name] += 1
    return dict(counts)
