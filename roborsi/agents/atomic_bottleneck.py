"""Per-atomic bottleneck tracking — when an atomic repeatedly fails inside
LH runs, the LH Planner (Planner.decompose) should switch to FOCUS mode
and attack that atomic
in isolation before continuing the full LH.

Persistent in repo (real learned knowledge):
  roborsi/embodied/skills/_data/atomic_bottleneck.jsonl

Each record is one atomic attempt outcome inside an LH:
  {ts, lh_task, atomic, success: bool, attempts_used,
   source_run_id, blocking_skill_sha}

Bottleneck classification (read-side):
  An atomic is BOTTLENECKED if, across the last N=10 LH runs that
  touched it, success_rate < 0.30 OR has 3+ consecutive crash outcomes.

Operator flow:
  - LH runs → record_atomic_outcome() after each atomic.
  - Next Planner.decompose call → get_bottleneck_atomics() → if any atomic in
    proposed plan is bottlenecked, switch to FOCUS mode for that atomic.
  - Focus mode runs ONLY that atomic with 10 retries + force propose
    on every failure. After 1 success, mark_resolved(); next LH plan
    proceeds normally.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from roborsi.agents.skill_history import _current_commit_sha, _REPO


_LOG = (_REPO / "roborsi" / "embodied" / "skills" / "_data"
        / "atomic_bottleneck.jsonl")


def record_atomic_outcome(*, lh_task: str, atomic: str,
                            success: bool, attempts_used: int,
                            source_run_id: str = "",
                            blocking_skill: str = "") -> None:
    """Append one atomic-outcome record. Called by LHExecutor after each
    atomic's retry loop ends (success OR exhausted retries)."""
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": int(time.time()),
        "lh_task": lh_task,
        "atomic": atomic,
        "success": bool(success),
        "attempts_used": int(attempts_used),
        "source_run_id": source_run_id,
        "blocking_skill_sha": _current_commit_sha(blocking_skill) or "",
    }
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def get_atomic_stats(window: int = 10) -> dict[str, dict]:
    """Aggregate the last `window` outcomes per atomic.
    Returns {atomic_name: {n, n_success, n_failed, recent_failures_streak}}."""
    if not _LOG.exists():
        return {}
    per_atomic: dict[str, list] = defaultdict(list)
    with _LOG.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            per_atomic[rec.get("atomic", "")].append(rec)
    out: dict[str, dict] = {}
    for name, recs in per_atomic.items():
        recent = recs[-window:]
        streak = 0
        for r in reversed(recent):
            if r.get("success"):
                break
            streak += 1
        out[name] = {
            "n": len(recent),
            "n_success": sum(1 for r in recent if r.get("success")),
            "n_failed": sum(1 for r in recent if not r.get("success")),
            "recent_failures_streak": streak,
            "avg_attempts": sum(r.get("attempts_used", 0) for r in recent) / len(recent),
        }
    return out


def get_bottleneck_atomics(window: int = 10,
                            success_rate_threshold: float = 0.30,
                            min_attempts_to_judge: int = 3,
                            crash_streak_threshold: int = 3) -> dict[str, dict]:
    """Return subset of get_atomic_stats() that satisfy bottleneck criteria.

    An atomic is bottlenecked if:
      - it has at least min_attempts_to_judge recent outcomes AND
      - (success_rate < threshold OR consecutive failure streak >= crash_streak_threshold)
    """
    stats = get_atomic_stats(window)
    bottlenecks: dict[str, dict] = {}
    for name, s in stats.items():
        if s["n"] < min_attempts_to_judge:
            continue
        rate = s["n_success"] / s["n"] if s["n"] else 0
        if rate < success_rate_threshold or s["recent_failures_streak"] >= crash_streak_threshold:
            s = dict(s)
            s["success_rate"] = rate
            bottlenecks[name] = s
    return bottlenecks


def format_for_planner(bottlenecks: dict[str, dict]) -> str:
    """Render bottleneck atomics for inclusion in the LH Planner's (Planner.decompose) user prompt."""
    if not bottlenecks:
        return ""
    rows = ["=== ATOMIC BOTTLENECKS (consider FOCUS mode) ==="]
    for name, s in sorted(bottlenecks.items(),
                            key=lambda kv: (kv[1]["success_rate"], -kv[1]["recent_failures_streak"])):
        rows.append(
            f"  {name}: success_rate={s['success_rate']:.2f} "
            f"(n={s['n']}) · failure_streak={s['recent_failures_streak']} · "
            f"avg_attempts={s['avg_attempts']:.1f}"
        )
    rows.append(
        "If any atomic above appears in your plan, OUTPUT a FOCUS-only "
        "spec: ordered_atomics with JUST that atomic, success_criteria = "
        "'verify this atomic works in isolation before re-running full LH'."
    )
    return "\n".join(rows)


def mark_resolved(atomic: str) -> None:
    """Append a success record so the streak resets. Called by LHExecutor
    when a previously-bottlenecked atomic succeeds in FOCUS mode."""
    record_atomic_outcome(
        lh_task="(focus)", atomic=atomic,
        success=True, attempts_used=1,
        source_run_id="focus-resolution",
    )
