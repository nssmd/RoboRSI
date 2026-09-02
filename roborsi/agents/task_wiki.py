"""task_wiki — per-task accumulated knowledge accessible by Planner /
Engineer / Reviewer.

Wiki content (per 2026-06-15 user-defined scope; 2026-07-03 review gate):
1. Successful execution traces (auto-recorded by harness on atomic done)
2. Failed execution traces — OBSERVED FACTS ONLY (seed, outcome, tool
   sequence). The Reviewer's interpretation (root_cause/next_action) is NOT
   written here; it is queued to wiki_review and only enters the wiki after a
   Manager approves it (→ 'Manager-approved leads'). Writing to the wiki body
   requires review — both Reviewer (author) and Manager (approver) must look.
3. Manager-approved leads (Reviewer-hypothesised, Manager-approved)
4. Key measurements (Reviewer-proposed, human-approved via wiki_review queue)

NOT in wiki: hand-written strategy markdown. Strategies belong in
base/compound skills. Wiki is execution history + measurements only.

Layout:
  <skill_dir>/wiki.md                   — the wiki itself (markdown), lives
                                          inside the task's skill dir so it
                                          ships with the skill (git + cold
                                          start). <skill_dir> is the task's
                                          zeroshot/ or execute/ directory.
  <skill_dir>/wiki_archive.md           — older entries trimmed off the cap
  ~/.roborsi/wiki_review/<id>.json   — Reviewer-proposed measurement queue
                                          (runtime approval queue — stays out
                                          of the skill dir)
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WIKI_REVIEW_ROOT = Path.home() / ".roborsi" / "wiki_review"
# Plan-promotion queue: a Sim-SUCCESS run's workspace plan proposed for
# promotion into the task's persistent (read-only) seed plan.md. Manager
# approval via resolve_plan_promotion is the ONLY path that overwrites the
# seed — mirrors the wiki_review gate (nothing unverified persists).
PLAN_REVIEW_ROOT = Path.home() / ".roborsi" / "plan_review"
# Policy-proposal queue: on a task with several Sim successes, the Planner may
# author a solidified compound (policy.py + SKILL.md) that codifies the winning
# recipe as one Engineer-callable tool. It is queued here; a Manager approves via
# resolve_policy_proposal — the ONLY path that writes a compound into the repo.
POLICY_REVIEW_ROOT = Path.home() / ".roborsi" / "policy_review"

# Caps per section so the wiki stays Engineer-friendly (~1.5k tokens
# total). Per 2026-06-15 user request "Wiki 强读加 Cap". Older entries
# move to <skill_dir>/wiki_archive.md for Planner offline browsing.
MAX_SUCCESS_TRACES = 3
MAX_FAILURE_TRACES = 3
# Measurements are not capped — they're dense reference facts.

_TEMPLATE = """# Wiki · {task}

Per-task accumulated knowledge. Read-only reference for Planner /
Engineer / Reviewer.

TRUST HIERARCHY (read this before believing any entry below):
  1. Successful execution traces + Key measurements + Manager-approved leads =
     SIM-VERIFIED / Manager-approved FACTS. Trust them.
  2. Failed execution traces record OBSERVED FACTS ONLY (seed, outcome, tool
     sequence). The Reviewer's interpretation is NOT here — it is held in the
     wiki_review queue until a Manager approves it, precisely because wrong
     hypotheses (e.g. "the receiver is a decoy") have poisoned this loop before.
     An approved lead moves into 'Manager-approved leads'; nothing unreviewed
     ever steers a plan.

## Successful execution traces

(empty — populated on first atomic success)

## Failed execution traces

(empty — OBSERVED facts only; Reviewer diagnosis stays in wiki_review until approved)

## Manager-approved leads

(empty — populated when a Manager approves a queued failure hypothesis)

## Key measurements (Reviewer-proposed, human-approved)

(empty — populated when Reviewer files a measurement and you approve it)
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_skill_dir(task: str) -> Path:
    """Return the skill directory that owns this task's persistent docs
    (wiki.md / wiki_archive.md / plan.md).

    A task is published as a skill whose entry point is either its
    ``.zeroshot`` (atomic) or ``.execute`` (long-horizon) subskill. The
    persistent docs live next to that subskill's SKILL.md so they ship
    with the skill (git + cold start). Tries ``<task>.zeroshot`` first,
    then ``<task>.execute``; raises if neither is found.
    """
    from roborsi.embodied.skills import get
    # Some tasks run under a BiCoord env whose name carries a "_bicoord" suffix
    # (e.g. handover_block -> handover_block_bicoord in the rollout's env mapping)
    # while the SKILL is registered under the BASE name. Try the given name, then
    # the de-suffixed base as a fallback, so the alias/registry mismatch does not
    # crash the episode before it starts. Tasks registered WITH the suffix
    # (e.g. stack_bowls_bicoord) resolve on the first try and never hit fallback.
    names = [task]
    if task.endswith("_bicoord"):
        names.append(task[: -len("_bicoord")])
    for name in names:
        for suffix in ("zeroshot", "execute"):
            sk = get(f"{name}.{suffix}")
            if sk is not None:
                return sk.path.parent
    raise ValueError(
        f"no '{task}.zeroshot' or '{task}.execute' skill found — "
        f"cannot resolve persistent doc dir for task '{task}'")


def wiki_path(task: str) -> Path:
    return _task_skill_dir(task) / "wiki.md"


def _ensure_wiki(task: str) -> Path:
    p = wiki_path(task)
    if not p.exists():
        p.write_text(_TEMPLATE.format(task=task), encoding="utf-8")
    return p


def _insert_under_section(wiki_md: str, section_title: str,
                          new_block: str) -> str:
    """Insert new_block right after the section heading. If the section
    body contains the '(empty — ...)' placeholder, replace it.

    Matches a heading line that STARTS WITH `## <section_title>` so
    parenthetical suffixes in the heading (e.g. "(Reviewer-proposed)")
    are preserved.
    """
    lines = wiki_md.split("\n")
    head_idx = -1
    for i, ln in enumerate(lines):
        if ln.startswith(f"## {section_title}"):
            head_idx = i
            break
    if head_idx < 0:
        # Append section if missing.
        return wiki_md.rstrip() + f"\n\n## {section_title}\n\n{new_block.strip()}\n"
    # Find next "## " heading or end of file for section end.
    end_idx = len(lines)
    for j in range(head_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    section_body = lines[head_idx + 1:end_idx]
    # Strip leading blanks + placeholder line(s).
    body_keep: list[str] = []
    skipping_placeholder = True
    for ln in section_body:
        if skipping_placeholder:
            if ln.strip() == "":
                continue
            if ln.strip().startswith("(empty"):
                # also skip a following blank
                continue
            skipping_placeholder = False
        body_keep.append(ln)
    out = (lines[:head_idx + 1]
            + [""]
            + new_block.strip().split("\n")
            + [""]
            + body_keep
            + lines[end_idx:])
    return "\n".join(out)


def _tool_sequence_to_md(events: list[dict]) -> str:
    """Render a tool-sequence list (each event has tool + args dict) as md."""
    lines = []
    for i, ev in enumerate(events):
        tool = ev.get("tool", "?")
        args = ev.get("args") or {}
        if args:
            args_line = ", ".join(f"{k}={v}" for k, v in args.items())
            lines.append(f"  {i+1}. `{tool}` ({args_line})")
        else:
            lines.append(f"  {i+1}. `{tool}`")
    return "\n".join(lines)


def _trim_section_to_cap(wiki_md: str, section_title: str,
                          max_entries: int, task: str) -> str:
    """Keep at most `max_entries` entries (### headings) in a section.
    Older entries (above the cap) get appended to wiki_archive/<task>.md
    so Planner can browse history offline without bloating Engineer's
    instruction.
    """
    lines = wiki_md.split("\n")
    head_idx = -1
    for i, ln in enumerate(lines):
        if ln.startswith(f"## {section_title}"):
            head_idx = i
            break
    if head_idx < 0:
        return wiki_md
    end_idx = len(lines)
    for j in range(head_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    # Find ### sub-headings within section.
    entry_starts = [k for k in range(head_idx + 1, end_idx)
                    if lines[k].startswith("### ")]
    if len(entry_starts) <= max_entries:
        return wiki_md

    # Keep newest max_entries (assume entries added at top → newest first).
    keep_start = entry_starts[0]
    last_kept_entry_idx = entry_starts[max_entries - 1]
    # Find where the last kept entry ends (= next entry's start, or end_idx).
    if max_entries < len(entry_starts):
        keep_end = entry_starts[max_entries]
    else:
        keep_end = end_idx
    # Archive everything from keep_end to end_idx.
    archived_block = "\n".join(lines[keep_end:end_idx])
    if archived_block.strip():
        ar = _task_skill_dir(task) / "wiki_archive.md"
        ar_existing = ar.read_text(encoding="utf-8") if ar.exists() else ""
        ar.write_text(
            ar_existing
            + f"\n\n## Archived {section_title} ({_now_iso()})\n\n"
            + archived_block + "\n",
            encoding="utf-8")
    new_lines = lines[:keep_end] + lines[end_idx:]
    return "\n".join(new_lines)


def append_success_trace(*, task: str, atomic: str, seed: int,
                          run_id: str, tool_events: list[dict],
                          tool_calls_total: int) -> Path:
    """Append a successful execution trace to the wiki (capped)."""
    p = _ensure_wiki(task)
    md = p.read_text(encoding="utf-8")
    seq_md = _tool_sequence_to_md(tool_events)
    block = (
        f"### {atomic} · seed={seed} · run={run_id} · {_now_iso()}\n"
        f"- tool_calls: {tool_calls_total}\n"
        f"- outcome: ✓ success\n"
        f"- sequence:\n{seq_md}\n"
    )
    new_md = _insert_under_section(md, "Successful execution traces", block)
    new_md = _trim_section_to_cap(new_md, "Successful execution traces",
                                   MAX_SUCCESS_TRACES, task)
    p.write_text(new_md, encoding="utf-8")
    return p


def append_failure_trace(*, task: str, atomic: str, seed: int,
                          run_id: str, tool_events: list[dict],
                          tool_calls_total: int,
                          reviewer_root_cause: str,
                          reviewer_next_action: str) -> Path:
    """Append a failed run's OBSERVED FACTS (seed, outcome, tool sequence) to the
    wiki. The Reviewer's INTERPRETATION (root_cause + next_action) is an
    UNVERIFIED HYPOTHESIS from a single failed run — a guess, NOT a fact — so it
    is NEVER written into the wiki body here. It is queued to wiki_review for
    Manager sign-off, and ONLY an APPROVED hypothesis is written into the wiki's
    'Manager-approved leads' section (via resolve_wiki_hypothesis). A wrong guess
    (which once poisoned this loop by calling the real receiver a 'decoy') thus
    cannot silently become authoritative task knowledge that the Planner reads:
    both the Reviewer (author) and the Manager (approver) must look first.
    """
    review_path = _enqueue_hypothesis_review(
        task=task, run_id=run_id,
        root_cause=reviewer_root_cause, next_action=reviewer_next_action)
    pid = review_path.stem
    p = _ensure_wiki(task)
    md = p.read_text(encoding="utf-8")
    seq_md = _tool_sequence_to_md(tool_events)
    block = (
        f"### {atomic} · seed={seed} · run={run_id} · {_now_iso()}\n"
        f"- tool_calls: {tool_calls_total}\n"
        f"- outcome: ✗ failure\n"
        f"- reviewer diagnosis: [PENDING REVIEW — root_cause + next_action are "
        f"queued to wiki_review/{pid}; NOT shown as a lead until a Manager "
        f"approves them, so an unverified guess can't steer the next plan]\n"
        f"- sequence:\n{seq_md}\n"
    )
    new_md = _insert_under_section(md, "Failed execution traces", block)
    new_md = _trim_section_to_cap(new_md, "Failed execution traces",
                                   MAX_FAILURE_TRACES, task)
    p.write_text(new_md, encoding="utf-8")
    return p


def _enqueue_hypothesis_review(*, task: str, run_id: str,
                                root_cause: str, next_action: str) -> Path:
    """Queue a failed-run Reviewer hypothesis for Manager review, so the Manager
    (cron self-check) sees every unverified next_action and can approve or
    reject it against sim ground truth."""
    WIKI_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    pid = f"{int(time.time())}-hyp-{uuid.uuid4().hex[:6]}"
    p = WIKI_REVIEW_ROOT / f"{pid}.json"
    payload = {
        "id": pid,
        "kind": "failure_hypothesis",
        "task": task,
        "source_run_id": run_id,
        "root_cause": root_cause,
        "next_action": next_action,
        "created_at": _now_iso(),
        "status": "pending",
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    return p


def resolve_wiki_hypothesis(proposal_path: Path, *, approve: bool,
                             manager_note: str = "") -> Path:
    """Manager verdict on a queued failure hypothesis. This is the ONLY path by
    which a Reviewer failure hypothesis enters the wiki body. approve=True writes
    the root_cause + next_action into the wiki's 'Manager-approved leads' section
    (now a trusted, human-signed-off lead). approve=False leaves the wiki
    untouched — the unverified guess never entered it, so nothing to retract.
    Marks the proposal resolved either way."""
    payload = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
    task = payload["task"]
    p = _ensure_wiki(task)
    if approve:
        md = p.read_text(encoding="utf-8")
        block = (
            f"- [{payload['source_run_id']}] "
            f"{payload['next_action'].strip()}\n"
            f"  - root_cause: {payload['root_cause'].strip()}\n"
            f"  - approved {_now_iso()}"
            + (f" · {manager_note}" if manager_note else "") + "\n"
        )
        p.write_text(_insert_under_section(md, "Manager-approved leads", block),
                     encoding="utf-8")
    payload["status"] = "approved" if approve else "rejected"
    payload["manager_note"] = manager_note
    payload["resolved_at"] = _now_iso()
    Path(proposal_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def _enqueue_plan_promotion(*, task: str, run_id: str, workspace_plan_md: str,
                             rationale: str, engineer_replanned: bool = False,
                             reason_for_revision: str = "") -> Path | None:
    """Queue a Sim-SUCCESS run's workspace plan for promotion into the task's
    persistent (read-only) seed plan.md — a Manager approves via
    resolve_plan_promotion before the seed is ever overwritten. No-op (returns
    None) when the workspace plan is byte-identical to the current seed: there
    is nothing to promote, so the queue stays signal-rich (only genuinely
    changed plans surface). The prior seed is stashed in the payload so an
    approved promotion can be rolled back."""
    from roborsi.agents.planner import persistent_plan_path
    seed_path = persistent_plan_path(task)
    prior = seed_path.read_text(encoding="utf-8") if seed_path.exists() else ""
    if workspace_plan_md == prior:
        return None
    PLAN_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    pid = f"{int(time.time())}-plan-{uuid.uuid4().hex[:6]}"
    p = PLAN_REVIEW_ROOT / f"{pid}.json"
    payload = {
        "id": pid,
        "kind": "plan_promotion",
        "task": task,
        "source_run_id": run_id,
        "workspace_plan_md": workspace_plan_md,
        "prior_persistent_md": prior,
        "rationale": rationale,
        "engineer_replanned": engineer_replanned,
        "reason_for_revision": reason_for_revision,
        "created_at": _now_iso(),
        "status": "pending",
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    return p


def resolve_plan_promotion(proposal_path: Path, *, approve: bool,
                            manager_note: str = "") -> Path:
    """Manager verdict on a queued plan promotion. This is the ONLY path that
    overwrites a task's persistent (read-only) seed plan.md. approve=True writes
    the run's workspace plan over the seed; approve=False leaves the seed
    untouched (the unproven plan never entered it). Marks the proposal resolved
    either way. Mirrors resolve_wiki_hypothesis — does NOT move the file (the
    Manager cron archives resolved proposals to applied/)."""
    from roborsi.agents.planner import persistent_plan_path
    payload = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
    if approve:
        persistent_plan_path(payload["task"]).write_text(
            payload["workspace_plan_md"], encoding="utf-8")
    payload["status"] = "approved" if approve else "rejected"
    payload["manager_note"] = manager_note
    payload["resolved_at"] = _now_iso()
    Path(proposal_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return Path(proposal_path)


def propose_measurement(*, task: str, measurement_md: str, rationale: str,
                        source_run_id: str, reviewer: str) -> Path:
    """Queue a Reviewer-proposed key measurement for human approval.

    Returns the path of the wiki_review JSON. Use
    `apply_measurement_proposal(path)` after human review."""
    WIKI_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    pid = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    p = WIKI_REVIEW_ROOT / f"{pid}.json"
    payload = {
        "id": pid,
        "task": task,
        "measurement_md": measurement_md,
        "rationale": rationale,
        "source_run_id": source_run_id,
        "reviewer": reviewer,
        "created_at": _now_iso(),
        "status": "pending",
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def apply_measurement_proposal(proposal_path: Path) -> Path:
    """Apply an approved measurement proposal to the wiki."""
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    task = payload["task"]
    p = _ensure_wiki(task)
    md = p.read_text(encoding="utf-8")
    block = (
        f"- {payload['measurement_md'].strip()}\n"
        f"  - source: `{payload['source_run_id']}` · "
        f"by `{payload.get('reviewer','?')}` · "
        f"approved {_now_iso()}\n"
    )
    new_md = _insert_under_section(md, "Key measurements", block)
    p.write_text(new_md, encoding="utf-8")
    # Mark proposal as applied.
    payload["status"] = "applied"
    payload["applied_at"] = _now_iso()
    proposal_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def read_wiki(task: str) -> str:
    """Return wiki markdown for a task (empty template if not yet created).
    '' if the task has no .zeroshot/.execute skill dir yet — the Planner now
    calls this before a skill may exist and must not crash on that."""
    try:
        p = _ensure_wiki(task)
    except ValueError:
        return ""
    raw = p.read_text(encoding="utf-8")
    from roborsi.agents.gt_firewall import redact
    clean, _dropped = redact(task, raw)
    return clean


import re as _re

# A compound directory name must be a plain identifier (no path traversal, not
# the LLM 'zeroshot' lane) — enforced before any write into the repo tree.
_COMPOUND_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def compound_dir(task: str, name: str) -> Path:
    """Where a solidified compound ships: atomic/<task>/<name>/. Rejects unsafe
    names and the reserved 'zeroshot' lane."""
    if not _COMPOUND_NAME_RE.match(name) or name in ("zeroshot", "execute"):
        raise ValueError(f"unsafe/reserved compound name {name!r}")
    return _task_skill_dir(task).parent / name


def _enqueue_policy_proposal(*, task: str, run_id: str, compound_name: str,
                             policy_code: str, skill_md: str, rationale: str,
                             success_count: int) -> Path:
    """Queue a Planner-authored compound policy for Manager review. Nothing is
    written into the repo here — the code + SKILL.md sit in the queue until a
    Manager runs the harness gate and approves via resolve_policy_proposal."""
    compound_dir(task, compound_name)   # validate name early (raises if unsafe)
    POLICY_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    pid = f"{int(time.time())}-policy-{uuid.uuid4().hex[:6]}"
    p = POLICY_REVIEW_ROOT / f"{pid}.json"
    payload = {
        "id": pid,
        "kind": "compound_policy",
        "task": task,
        "compound_name": compound_name,
        "source_run_id": run_id,
        "policy_code": policy_code,
        "skill_md": skill_md,
        "rationale": rationale,
        "success_count": success_count,
        "created_at": _now_iso(),
        "status": "pending",
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    return p


def resolve_policy_proposal(proposal_path: Path, *, approve: bool,
                            manager_note: str = "") -> Path:
    """Manager verdict on a queued compound policy. This is the ONLY path that
    writes a compound into atomic/<task>/<name>/. approve=True materialises
    policy.py + SKILL.md there (the Manager commits them after
    the harness gate); approve=False leaves the repo untouched. Marks the
    proposal resolved either way — mirrors resolve_wiki_hypothesis, does NOT
    move the file (the Manager cron archives resolved proposals to applied/)."""
    payload = json.loads(Path(proposal_path).read_text(encoding="utf-8"))
    if approve:
        d = compound_dir(payload["task"], payload["compound_name"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "policy.py").write_text(payload["policy_code"], encoding="utf-8")
        (d / "SKILL.md").write_text(payload["skill_md"], encoding="utf-8")
    payload["status"] = "approved" if approve else "rejected"
    payload["manager_note"] = manager_note
    payload["resolved_at"] = _now_iso()
    Path(proposal_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return Path(proposal_path)
