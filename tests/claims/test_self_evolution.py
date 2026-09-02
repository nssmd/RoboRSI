"""Claim 1: self-evolution thresholds.

Reads the latest ``selfevo-round{K}`` benches from sqlite (produced by
``roborsi selfevo run``) and asserts the four target thresholds.

These tests do NOT run the loop themselves (that would take hours). Run
the loop yourself first:

    roborsi selfevo run \\
        --train click_bell,beat_block_hammer,pick_block_bicoord \\
        --test  pick_bowl_bicoord,stack_bowls_bicoord \\
        --rounds 3 --seeds 5

Then ``pytest tests/claims/test_self_evolution.py``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roborsi.store import trace_db as _td


# ── thresholds from TODO.md ───────────────────────────────────────────
DELTA_RATE_PP        = 0.30      # mean(rate_K - rate_0) ≥ +30 pp
CALLS_RATIO_MAX      = 0.60      # mean(calls_K / calls_0) ≤ 0.6
MIN_NEW_BASE_SKILLS  = 5
MIN_REUSE_FRACTION   = 0.50      # ≥50% of new base skills called in test runs

ROUND_K = 3                       # the "final" round to compare against round 0


@pytest.fixture(scope="module")
def selfevo_rows() -> list[dict]:
    """All selfevo-round* bench rows, newest first per (skill, tag)."""
    _td.init()
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM benches WHERE tag LIKE 'selfevo-round%' "
        "ORDER BY tag, skill, run_at DESC").fetchall()]
    c.close()
    if not rows:
        pytest.skip("no selfevo benches recorded — run "
                     "`roborsi selfevo run` first")
    return rows


def _rate(r: dict) -> float:
    return (r["seeds_passed"] or 0) / max(1, r["seeds_total"])


def _split_by_tag(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["tag"], []).append(r)
    return out


def test_test_set_rate_improvement(selfevo_rows):
    """Mean test-set success rate should jump ≥30pp from round 0 to round K."""
    by_tag = _split_by_tag(selfevo_rows)
    r0 = by_tag.get("selfevo-round0", [])
    rk = by_tag.get(f"selfevo-round{ROUND_K}", [])
    assert r0, "missing round 0 baseline"
    assert rk, f"missing round {ROUND_K} bench"
    avg_r0 = sum(_rate(r) for r in r0) / len(r0)
    avg_rk = sum(_rate(r) for r in rk) / len(rk)
    delta = avg_rk - avg_r0
    assert delta >= DELTA_RATE_PP, (
        f"test-set rate improvement {delta*100:+.1f}pp < target "
        f"{DELTA_RATE_PP*100:.0f}pp  (round0={avg_r0*100:.0f}%, "
        f"round{ROUND_K}={avg_rk*100:.0f}%)")


def test_test_set_tool_calls_drop(selfevo_rows):
    """Mean test-set avg tool calls should be ≤60% of baseline."""
    by_tag = _split_by_tag(selfevo_rows)
    r0 = by_tag.get("selfevo-round0", [])
    rk = by_tag.get(f"selfevo-round{ROUND_K}", [])
    assert r0 and rk
    c0 = [r["avg_tool_calls"] or 0 for r in r0]
    ck = [r["avg_tool_calls"] or 0 for r in rk]
    avg0 = sum(c0) / len(c0)
    avgk = sum(ck) / len(ck)
    if avg0 == 0:
        pytest.skip("round 0 had 0 tool calls — can't compute ratio")
    ratio = avgk / avg0
    assert ratio <= CALLS_RATIO_MAX, (
        f"calls ratio {ratio:.2f} > target {CALLS_RATIO_MAX}  "
        f"(round0={avg0:.1f}, round{ROUND_K}={avgk:.1f})")


def test_new_base_skill_count_minimum(selfevo_rows):
    """Round K should have produced at least MIN_NEW_BASE_SKILLS new base
    skills via applied proposals."""
    _td.init()
    proposals = _td.list_proposals(status="applied", limit=500)
    applied_base = [p for p in proposals
                     if (p.get("skill") or "").startswith("base.")
                     and p.get("kind") == "new"]
    assert len(applied_base) >= MIN_NEW_BASE_SKILLS, (
        f"only {len(applied_base)} new base skills applied "
        f"(want ≥{MIN_NEW_BASE_SKILLS})")


def test_new_base_skill_reuse(selfevo_rows):
    """At least MIN_REUSE_FRACTION of the new base skills must appear as
    inner tool calls during round-K test rollouts."""
    _td.init()
    proposals = _td.list_proposals(status="applied", limit=500)
    base_names = [
        (p.get("skill") or "").removeprefix("base.")
        for p in proposals
        if (p.get("skill") or "").startswith("base.")
        and p.get("kind") == "new"
    ]
    if not base_names:
        pytest.skip("no applied new base skills to measure reuse against")
    # Pull inner steps from round-K test runs.
    by_tag = _split_by_tag(selfevo_rows)
    rk = by_tag.get(f"selfevo-round{ROUND_K}", [])
    skills_in_rk = {r["skill"] for r in rk}
    # All inner-tool names seen during runs of those skills (any time).
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    tools_seen: set[str] = set()
    for sk in skills_in_rk:
        # Look at the most recent round-K runs only (heuristic — last 10).
        for run in _td.list_runs(skill=sk, limit=10):
            steps = _td.list_steps(run_id=run["id"], layer="inner")
            for s in steps:
                if s.get("tool"):
                    tools_seen.add(s["tool"])
    c.close()
    used = [n for n in base_names if n in tools_seen]
    fraction = len(used) / len(base_names)
    assert fraction >= MIN_REUSE_FRACTION, (
        f"only {len(used)}/{len(base_names)} new base skills called in "
        f"round-{ROUND_K} test runs ({fraction*100:.0f}% < "
        f"{MIN_REUSE_FRACTION*100:.0f}%)")
