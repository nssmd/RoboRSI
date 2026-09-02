"""Planner-authored compound-policy proposals — the self-evolution loop that
turns a task's proven successes into a solidified, Engineer-callable macro.

When a task has enough Sim-verified successes and no compound yet, the Planner is
ENCOURAGED (a prompt slot) to distil the winning recipe into a
``compound policy.py`` proposal. Its output is parsed here and queued to
``policy_review``; a Manager approves via
:func:`roborsi.agents.task_wiki.resolve_policy_proposal`, which is the ONLY
path that writes a compound into ``atomic/<task>/<name>/``.

Opt-in via ``ROBORSI_ATOMIC_COMPOUND`` (same gate as compound dispatch) so the
live campaign's Planner prompts are unchanged until the loop is switched on.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# A task needs at least this many recorded Sim successes before we ask the
# Solidify ONLY a recipe that succeeds RELIABLY (the user's 固化 bar: "确定能
# 稳定成功" — not a one-off). Require this many predicate-verified Sim successes
# AND at least this success rate over the task's runs, from trace.db.
_MIN_SUCCESS = 3
_MIN_RATE = 0.5
_BEGIN = "=== COMPOUND PROPOSAL ==="
_END = "=== END COMPOUND PROPOSAL ==="
_POLICY_MARK = "--- policy.py ---"
_SKILL_MARK = "--- SKILL.md ---"
_SUCCESS_PREDICATE = '%"predicate_check": true%'


def _enabled() -> bool:
    return os.environ.get("ROBORSI_ATOMIC_COMPOUND") == "1"


def _success_count(wiki_md: str) -> int:
    """Recorded Sim successes for the task (from its wiki success traces)."""
    return wiki_md.count("outcome: ✓ success")


def _stable_success(task: str) -> bool:
    """True only if the task succeeds RELIABLY in sim — enough predicate-verified
    successes AND a success rate showing it is not a lucky one-off. This is the
    固化 gate: solidify a compound only for a recipe that STABLY works, never for
    a single or flaky success (place_a2b_left passing seed=1 but failing seed=0 is
    exactly the "not stable yet" case this rejects)."""
    from roborsi.store import trace_db
    trace_db.init()
    row = trace_db._conn().execute(
        "SELECT SUM(CASE WHEN status='success' AND episode_summary_json LIKE ? "
        "THEN 1 ELSE 0 END) AS ok, COUNT(*) AS total FROM runs WHERE task = ?",
        (_SUCCESS_PREDICATE, task),
    ).fetchone()
    ok = (row["ok"] or 0) if row else 0
    total = (row["total"] or 0) if row else 0
    return ok >= _MIN_SUCCESS and total > 0 and (ok / total) >= _MIN_RATE


def encourage_block(task: str, wiki_md: str) -> str:
    """Prompt slot nudging the Planner to author a compound proposal. Empty
    unless opt-in AND the task succeeds STABLY (>= _MIN_SUCCESS verified Sim
    successes at >= _MIN_RATE rate) AND no compound exists yet — so it only fires
    for a recipe that reliably works, never a flaky/one-off success."""
    if not _enabled() or not _stable_success(task):
        return ""
    from roborsi.embodied.skills import discover_compounds
    if discover_compounds(task):
        return ""
    return (
        "=== OPTIONAL: PROPOSE A COMPOUND POLICY ===\n"
        f"This task now has >= {_MIN_SUCCESS} STABLE Sim-verified successes and no\n"
        "compound policy yet. If you can distil the winning recipe into a reusable coded macro\n"
        "the Engineer would call in ONE tool call (composing base skills via\n"
        "roborsi.embodied.skills._lib.solidified.pipeline), emit a proposal in\n"
        "EXACTLY this format at the END of your reply (or omit it entirely):\n"
        f"{_BEGIN}\n"
        "name: <lower_snake_case, e.g. pick_place>\n"
        "rationale: <one line — the winning sequence it codifies>\n"
        f"{_POLICY_MARK}\n"
        "<python defining dispatch_runtime(state, args) -> (result_dict, snapshot)>\n"
        f"{_SKILL_MARK}\n"
        "<YAML frontmatter (name/description/args/when_to_use) + a short body>\n"
        f"{_END}\n"
        "The Manager reviews it before it goes live. This is an ADDITIONAL block —\n"
        "do NOT alter your normal plan JSON/markdown output.\n\n"
    )


def capture(task: str, run_id: str, wiki_md: str, content: str) -> Path | None:
    """Parse a compound proposal out of the Planner's reply and queue it for
    review. Returns the queued path, or None if there is no (valid) proposal."""
    if not _enabled():
        return None
    parsed = _extract(content)
    if parsed is None:
        return None
    name, rationale, code, md = parsed
    from roborsi.agents.task_wiki import _enqueue_policy_proposal
    return _enqueue_policy_proposal(
        task=task, run_id=run_id, compound_name=name, policy_code=code,
        skill_md=md, rationale=rationale, success_count=_success_count(wiki_md))


def strip(content: str) -> str:
    """Remove the proposal block from Planner text so it never pollutes plan.md."""
    if _BEGIN not in content:
        return content
    head, _, rest = content.partition(_BEGIN)
    _, _, tail = rest.partition(_END)
    return (head + tail).strip()


def _extract(content: str) -> tuple[str, str, str, str] | None:
    if _BEGIN not in content or _END not in content:
        return None
    body = content.split(_BEGIN, 1)[1].split(_END, 1)[0]
    if _POLICY_MARK not in body or _SKILL_MARK not in body:
        return None
    m_name = re.search(r"name:\s*([a-z][a-z0-9_]{1,39})", body)
    if not m_name:
        return None
    m_rat = re.search(r"rationale:\s*(.+)", body)
    code = body.split(_POLICY_MARK, 1)[1].split(_SKILL_MARK, 1)[0].strip()
    md = body.split(_SKILL_MARK, 1)[1].strip()
    code = _unfence(code)
    md = _unfence(md)
    if not code or not md:
        return None
    return m_name.group(1), (m_rat.group(1).strip() if m_rat else ""), code, md


def _unfence(text: str) -> str:
    """Drop a surrounding ```lang ... ``` markdown fence if the model added one."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    lines = lines[1:]                       # drop opening ```lang
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
