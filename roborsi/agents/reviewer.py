"""Reviewer role — Opus reads plan/summary/trace and decides on proposal.

Outputs:
  - workspace/review.md (human readable)
  - optional ~/.roborsi/skill_review/<pid>.json (existing propose
    format; runs through scripts/apply_selfevo_proposal.py harness gate)

Reviewer is a third independent LLM call. It explicitly compares
plan.md (what Engineer intended) vs Engineer's summary/trace (what
actually happened) vs the gate_log for this run. It can choose:

  NO_PROPOSAL   — nothing actionable came out of this attempt
  SKILL_UPDATE  — patch an existing base/robotwin skill
  NEW_SKILL     — author a new base/robotwin skill
  NEW_COMPOUND  — package the successful trace as a higher-level skill

Filter mode for proposals (per user 2026-06-09 spec):
  - PARALLEL not sequential. User picks one of:
       FILTER_MODE = "human"  — proposal lands in skill_review/ AND
                                an HTML diff page is generated for
                                manual approval (current default).
       FILTER_MODE = "auto"   — Planner runs harness + similarity
                                checks (planner.validate_proposal),
                                auto-apply if both pass.
  The choice is per-process (env var ROBORSI_FILTER_MODE), not
  per-proposal — keep the decision UX simple.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from roborsi.agents.workspace import Workspace


_SYSTEM_PROMPT = """You are REVIEWER for a robotics atomic-task attempt.

Inputs you get:
  - plan.md (what Planner intended)
  - summary.md (what Engineer reported)
  - inner trace (per-step tool calls + results)
  - gate_log entries for this run (structural gate fires)

Decide ONE outcome and emit a single JSON object. Schema:

{
  "verdict": "done" | "continue" | "blocked",
  "root_cause": "one-line diagnosis (required on continue/blocked)",
  "next_action": "concrete next thing to try (≤200 chars)",
  "proposal_decision": "NO_PROPOSAL" | "SKILL_UPDATE" | "NEW_SKILL" | "NEW_COMPOUND" | "PATCH",
  "proposal_payload": {                              <-- only when ≠ NO_PROPOSAL
      "name": "<target skill name, or short slug for a PATCH>",
      "kind": "update" | "new" | "patch",
      "category": "base/robotwin",
      "rationale": "1-2 paragraphs · cite run_id / step idx evidence",
      "new_code": "<full policy.py source if SKILL_UPDATE or NEW_SKILL>",
      "skill_md": "<SKILL.md content if NEW_SKILL or NEW_COMPOUND — MUST include harness: block>",
      "target_path": "<repo-relative .py if PATCH — a shared lib / VLM-rules / adapter>",
      "old_string": "<exact snippet to replace if PATCH — copy VERBATIM, must be unique>",
      "new_string": "<replacement snippet if PATCH>"
  },
  "review_md": "human-readable markdown for review.md (3-8 lines)"
}

━━ YOU ARE PART OF A SELF-EVOLUTION LOOP. Two hard expansions of your power, and
   one hard limit: ━━

(A) YOU CAN TARGET MORE THAN skill policy.py. The real cause often lives OUTSIDE
    a skill — in the shared perception library, the VLM's rules/prompt, or the
    sim adapter. Use proposal_decision = PATCH to fix it there:
      proposal_payload = {target_path, old_string, new_string, rationale}
    old_string must match the target file EXACTLY (verbatim, unique) — it is an
    exact search-and-replace, so a mismatched or non-unique old_string is
    auto-rejected. Legitimate PATCH targets (examples):
      - roborsi/embodied/skills/base/_lib/libero/_perception.py  (localize / cloud / grasp cascade)
      - roborsi/embodied/agent_loop/config.py                    (the VLM PICK/PLACE rules text)
      - roborsi/embodied/skills/base/*/libero/policy.py          (a muscle)
    Prefer PATCH over SKILL_UPDATE when the fix is a few lines in a large file.

(B) EVERY code proposal is AUTO-VERIFIED before it can be applied: the harness
    copies the tree, applies your patch, and runs N pure-vision episodes
    before/after. A proposal that does NOT raise the success rate is REJECTED.
    So only propose a change you have concrete reason to believe will measurably
    help, and state the expected effect in rationale. A guess that regresses is
    worse than NO_PROPOSAL.

(C) *** PURE-VISION — YOU MAY NOT PROPOSE CHEATING. HARD LIMIT. ***
    This task runs PURE-VISION: object and container locations come ONLY from the
    camera (find_pixel / SAM3 / depth unprojection / point clouds). You are
    FORBIDDEN to propose ANY change that reads simulator ground truth. NEVER
    propose, in new_code OR a PATCH, anything that uses:
      - describe_scene / get_object_pose / read_object_pose
      - any *_pos / *_contain_region site / env.region_box(...) / raw_obs()[...]
      - place_object_in(object=<name>) or grasp_object(object=<name>) resolving a GT site
      - anything tagged source == "sim_ground_truth"
    If perception is FAILING (occlusion, look-alike objects, bad depth), the fix is
    BETTER PERCEPTION — a different camera view, lifting to clear occlusion, the
    SAM3 mask, robust median depth — NEVER ground truth. Any GT-routing proposal is
    auto-rejected AND flagged as a cheat attempt. Grabbing ground truth is the
    single most common way this loop fails; do not do it.


Rules:
  - Default proposal_decision to NO_PROPOSAL. Only propose when you can cite a
    SPECIFIC failure mode (or genuinely reusable success trace).
  - TOOL RELIABILITY: read the TOOL RELIABILITY tally above. If a base tool was
    called >=3 times and failed the majority (esp. one flagged TOOL-LEVEL
    SUSPECT), the failure is in the TOOL ITSELF, not the Engineer's strategy —
    replanning will NOT help. Begin root_cause with "[TOOL_BUG <tool> N/M]". If
    you can write the COMPLETE fixed policy.py, emit SKILL_UPDATE for that tool.
    If you cannot write the full fix, STILL keep the "[TOOL_BUG <tool> N/M]"
    prefix on root_cause (with NO_PROPOSAL) so the Manager sees the tool-level
    signal instead of it vanishing as a generic strategy failure.
  - SKILL_UPDATE requires full new_code (drop-in replacement).
  - NEW_SKILL / NEW_COMPOUND requires both new_code AND skill_md WITH a
    `harness:` block in the frontmatter (sim_task + args + pass_criteria).
  - Do NOT propose for failures in the ORCHESTRATION layer (LHExecutor /
    triangle plumbing / channel dispatch) — that is not a skill. BUT a
    base/robotwin TOOL that is itself broken IS a skill defect: propose it or
    tag "[TOOL_BUG ...]"; never dismiss a broken tool as "infra".
  - MISSING SKILL: read the MISSING-SKILL SIGNAL above. If a low-level PRIMITIVE
    (move_to_pose / move_fingertip_to) was blind-tried many times and mostly
    IK-failed, the Engineer was hand-rolling a maneuver that should be ONE
    dedicated skill (e.g. a pour/tip, a two-stage insert, a controlled slide).
    This is a capability GAP, not a broken tool. Emit proposal_decision =
    NEW_SKILL, begin root_cause with "[MISSING_SKILL <verb>]" (e.g.
    "[MISSING_SKILL tip-pour]"), and author the new skill's policy.py (search a
    reachable config internally — do NOT hardcode expert poses) + SKILL.md with
    a harness: block. If you cannot write the full skill now, still keep the
    "[MISSING_SKILL <verb>]" prefix (with NO_PROPOSAL) so the Manager sees the
    gap instead of it vanishing as a generic strategy failure.
  - PRIOR HISTORY / SESSION: you are given this task's recorded failure count +
    already Manager-APPROVED leads (see the PRIOR HISTORY block). Treat review as a
    SESSION over the task, NOT a one-shot single-run judgement. Do NOT re-file a
    root_cause/next_action that an approved lead already covers — the Manager rejects
    duplicates. Either (a) give a NEW pattern-level root_cause the prior leads missed
    (cite the recurrence across runs), or (b) if an approved lead was FOLLOWED this run
    and still failed, root_cause = why that lead is insufficient + a concrete refinement.
    A NO_PROPOSAL is better than a duplicate of a known lead.
  - Output ONLY the JSON object. No prose, no fences.

CRITICAL — new_code / skill_md CONTENT FORMAT:

  `new_code` MUST be a COMPLETE valid Python file that fully replaces the
  target skill's policy.py. NOT a diff. NOT a TODO comment. NOT prose
  describing what you want changed. The apply path writes this string
  verbatim to policy.py — if you submit a one-line comment, the existing
  237-line implementation gets wiped.

  GOOD `new_code` (acceptable):
    "from __future__ import annotations\\n\\ndef dispatch_runtime(state, args):\\n    arm = args.get('arm')\\n    # ... full working implementation ...\\n    return ({'ok': True, ...}, obs)\\n"

  BAD `new_code` (REJECTED on sight):
    "# Add: fallback to mesh rim sampling when contact_point is None"
    "TODO: handle dual-arm case"
    "Patch line 87 to check target_pose != None before move"

  If you do NOT have a complete working implementation in hand, choose
  proposal_decision = NO_PROPOSAL and put your suggested fix in
  next_action instead. A vague proposal is worse than no proposal.

  `skill_md` follows the same rule: complete YAML frontmatter + markdown
  body, not a list of changes you'd like to see."""


def _gate_log_for_run(run_id: str, limit: int = 20) -> list[dict]:
    """Pull last gate fires for the run_id from ~/.roborsi/gate_log.jsonl."""
    path = Path.home() / ".roborsi" / "gate_log.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id in str(rec):
                out.append(rec)
    return out[-limit:]


def _tool_reliability_summary(trace: list[dict]) -> str:
    """Per-tool call/fail tally from this run's trace — an OBJECTIVE signal so
    the Reviewer can tell a broken base TOOL (fix the tool) apart from an
    Engineer strategy error (replan). A tool called >=3x that fails the
    majority is a tool-level defect worth a proposal, not a NO_PROPOSAL shrug.
    """
    from collections import Counter
    called: Counter = Counter()
    failed: Counter = Counter()
    for s in trace:
        if not isinstance(s, dict):
            continue
        name = (s.get("tool_call") or {}).get("tool")
        if not name or name in ("look", "done", "describe_scene_actors", "read_task_wiki"):
            continue
        r = s.get("result") or {}
        called[name] += 1
        if isinstance(r, dict) and (
                r.get("ok") is False or r.get("still_on_table") is True
                or r.get("success") is False):
            failed[name] += 1
    if not called:
        return "(no actionable tool calls)"
    lines = []
    for name, n in called.most_common():
        f = failed[name]
        flag = "   <-- TOOL-LEVEL SUSPECT" if n >= 3 and f * 2 > n else ""
        lines.append(f"  {name}: {n} called, {f} failed{flag}")
    return "\n".join(lines)


def _missing_skill_signal(trace: list[dict]) -> str:
    """Detect the Engineer HAND-ROLLING a maneuver out of low-level primitives
    that should be ONE dedicated skill. Distinct from TOOL-LEVEL SUSPECT (a base
    *skill* that is broken): here a *primitive* (move_to_pose / move_fingertip_to)
    is being blind-tried as a substitute for a skill that does not exist yet.

    Heuristic: a primitive called >=6 times AND failing the majority means the
    Engineer is churning IK on poses a purpose-built skill would search once.
    That is exactly the signal that should have proposed tip_pour on
    dump_bin_bigbin (30+ move_to_pose IK-fails to find a tip pose). Emit a
    [MISSING_SKILL] hint so the Reviewer proposes a NEW_SKILL, not a shrug."""
    PRIMITIVES = {"move_to_pose", "move_fingertip_to"}
    from collections import Counter
    called: Counter = Counter()
    failed: Counter = Counter()
    for s in trace:
        if not isinstance(s, dict):
            continue
        name = (s.get("tool_call") or {}).get("tool")
        if name not in PRIMITIVES:
            continue
        r = s.get("result") or {}
        called[name] += 1
        note = str(r.get("note", "")) if isinstance(r, dict) else ""
        if (isinstance(r, dict) and r.get("ok") is False) or "DID NOT EXECUTE" in note:
            failed[name] += 1
    hits = [f"{n}: {called[n]} calls, {failed[n]} IK-fail/no-exec"
            for n in called if called[n] >= 6 and failed[n] * 2 > called[n]]
    if not hits:
        return "(none)"
    return ("PRIMITIVE CHURN — a dedicated skill is likely MISSING:\n  "
            + "\n  ".join(hits))


def _task_history_block(task: str) -> str:
    """Prior accumulated history for THIS task, so the Reviewer proposes at the
    PATTERN level ACROSS runs instead of re-deriving the same per-run hypothesis
    every seed. Feeds the count of past failed traces + the already Manager-
    APPROVED leads (known fixes it must not re-file). This is what makes the
    Reviewer a SESSION over the task's wiki, not a one-shot single-run reviewer."""
    from roborsi.agents.task_wiki import wiki_path
    p = wiki_path(task)
    if not p.exists():
        return "(no prior wiki — these are the first attempts on this task)"
    md = p.read_text(encoding="utf-8", errors="replace")
    n_fail = md.count("- outcome: ✗")
    leads = "(none approved yet)"
    if "## Manager-approved leads" in md:
        seg = md.split("## Manager-approved leads", 1)[1]
        seg = re.split(r"\n## ", seg, maxsplit=1)[0].strip()
        if seg:
            leads = seg[:2500]
    return (f"{n_fail} prior FAILED traces are recorded for this task.\n"
            f"Already Manager-APPROVED leads (KNOWN fixes — do NOT re-file these; the "
            f"Manager will reject a duplicate). Instead: propose a NEW pattern-level "
            f"root_cause the prior leads missed, OR if an approved lead was followed and "
            f"STILL failed, say WHY it is insufficient + a concrete refinement:\n{leads}")


def _sanitize_trace(trace: list[dict]) -> list[dict]:
    """PURE-VISION defense-in-depth: strip any residual privileged-ground-truth
    signatures from tool results before they reach the Reviewer prompt. The
    GT tools (describe_scene_actors etc.) are deleted, so in practice the trace
    only carries perception-derived data; this just guarantees a stray
    `actors`/contact-point/`sim_ground_truth` payload can never leak object GT
    to the Reviewer. Perceived coordinates (from find_pixel+unproject) are
    legitimate and kept."""
    GT_KEYS = {"actors", "contact_points_pose", "contact_point",
               "contact_points", "all_contacts"}
    def scrub(v):
        if isinstance(v, dict):
            if v.get("source") == "sim_ground_truth":
                return {"source": "sim_ground_truth", "_redacted": True}
            return {k: ("[redacted-GT]" if k in GT_KEYS else scrub(val))
                    for k, val in v.items()}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        return v
    return [scrub(step) if isinstance(step, dict) else step for step in trace]


def _parse_review_json(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction. Falls back to empty dict."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# ──────────────────────────────────────────────────────────────────────────
# Long-horizon reviewing — Reviewer.review_sub_atomic (per-attempt, in-session)
# + Reviewer.review_lh (final overall verdict). Same Reviewer class + same
# persistent (role=reviewer, task) session as .review(); only the prompts +
# trace serialization differ. There is no separate LHReviewer.
# ──────────────────────────────────────────────────────────────────────────


_SUB_REVIEWER_PROMPT = """You just observed atomic_{idx} ({atomic}) finish.
You can see the full trace in this conversation (you've been here from
the start of the LH task).

Decide: did this atomic ACTUALLY achieve its success criteria? Be
strict — Engineer may have called done(success=True) prematurely.

If you find a SYSTEMATIC base-skill bug (not a strategy mistake — an
actual skill .py issue, like wrong API, missing safety check, force-
closure miss, IK fallback gap), propose the fix RIGHT HERE — don't
wait for end-of-LH review. Mid-atomic proposals queue immediately so
the operator (human filter) or validator (auto filter) can act now.

The Reviewer's success criteria for this atomic were:
{criteria}

Reply with ONE JSON object, nothing else:

{{
  "verdict": "done" | "retry",
  "root_cause": "if retry — concise diagnosis (≤150 chars)",
  "next_action": "if retry — concrete change Engineer should try next attempt (≤200 chars)",
  "evidence": "what in the trace tells you this verdict (≤200 chars)",
  "proposal_decision": "NO_PROPOSAL" | "SKILL_UPDATE" | "NEW_SKILL" | "NEW_COMPOUND",
  "proposal_payload": {{
    "name": "<target base/robotwin skill name>",
    "kind": "update" | "new",
    "category": "base/robotwin",
    "rationale": "≤2 paragraphs · cite trace step / run_id evidence",
    "new_code": "<full policy.py if SKILL_UPDATE or NEW_SKILL>",
    "skill_md": "<SKILL.md frontmatter incl. harness: block if NEW_SKILL/COMPOUND>"
  }},
  "plan_amend": {{        // OPTIONAL — only when you want to modify the
                          // immutable sections of this atomic's plan.md
                          // (Goal / Hard rules / Done gate / Success criteria).
                          // Engineer can edit Recipe on its own via
                          // update_recipe tool; YOU file a plan_amend
                          // when one of the OTHER sections is wrong.
                          // Applied immediately to plan.md for next
                          // attempt + logged for audit.
    "section": "Goal" | "Hard rules" | "Done gate" | "Success criteria" | "Recipe",
    "new_text": "<full replacement text for that section, no heading>",
    "reason": "<one sentence why the section needed amending>"
  }}
}}

Rules:
- "done" iff EVERY success criterion holds, verified by tool results.
- "retry" if any criterion is unmet OR macro declared success without sim confirmation.
- "retry" is the safer default. Don't be diplomatic.
- GRIP-SLIP IS A REAL FAILURE: if the trace shows an arm started
  holding actor X but ended with X on table (actor.z near 0.74), that's
  a true fail — verdict=retry. NEVER suggest a sim cheat to "lock"
  the grip. The proper next_action is: re-grasp X then redo the place,
  using smooth single-shot moves instead of chained manual fingertip
  steps.
- NO SIM CHEATING: never propose using `use_attach=True`, force-grip
  overrides, teleport, or any physics override to "fix" grip-slip /
  IK failures. Those mask real agent bugs and don't transfer to the
  real robot. The fix is always: smarter motion plan, smarter recipe,
  smarter prompt — never a physics escape hatch.
- STRATEGY, NOT DATA in next_action: never paste literal actor xyz
  numbers in next_action (e.g. DON'T write "use xyz (-0.18,-0.19,0.76)").
  Actor positions are seed-random and your literal value only applies
  to this one run. INSTEAD say "call describe_scene_actors and use
  the <actor>.xyz from its return". next_action must describe a
  STRATEGY Engineer can execute regardless of current actor positions.

SKILL SELF-EVOLUTION — YOUR DUAL JOB:

  When a failure is a SYSTEMATIC base-skill bug (a real skill .py issue,
  not a strategy mistake), file the fix in THIS verdict: set
  proposal_decision = SKILL_UPDATE / NEW_SKILL / NEW_COMPOUND and fill
  proposal_payload with COMPLETE code. You have NO special skill tools —
  to inspect a skill before proposing, use your OWN Read/Grep on
  roborsi/embodied/skills/base/robotwin/<name>/. Don't bail to
  "NO_PROPOSAL + no code ready": read the closest existing skill, model
  your code on it, submit a real proposal. The 3-gate approval (harness
  auto + Claude similarity + Claude code review) keeps bad proposals out.

  ⚠ MANDATORY ESCALATION (per user 2026-06-16): if this atomic has
  already failed ≥2 times because of the SAME underlying SKILL
  limitation — a tool that times out on cuRobo, picks wrong grasp
  candidates, can't reach a pose, mis-grounds an object, etc. — you
  MUST stop giving "try another tool" next_actions and instead:
    1. Read the failing skill's policy.py yourself (Read/Grep),
    2. set proposal_decision = SKILL_UPDATE with the COMPLETE fixed
       policy.py in proposal_payload.new_code.
  The human operator ONLY reviews/approves your proposal — they will
  NOT hand-fix the skill for you. Reviewer-authored skill fixes are
  how this system self-evolves. A repeated skill-level failure with
  NO_PROPOSAL is itself a review failure on your part.
  (This applies ONLY to genuine skill-code limitations. If the failure
  is at the LH-executor/planner level — atomic ordering, state drift,
  Engineer operator error — that is NOT a skill bug; give a next_action
  instead.)

WIKI MEASUREMENTS — record what you measured:
  If this review PROVED a concrete durable constant (IK boundary, link
  offset, geometric constant, characteristic spawn height), state it
  plainly in `evidence` with the trace evidence proving it. The Manager
  records validated measurements into the per-task wiki (which informs
  future Planner + Engineer runs). Examples:
    - "Left-arm top-down IK floor at y=-0.15 is z≈0.78"
    - "Block actor spawns at z 0.74-0.78 across cuRobo init randomness"
  Do NOT state speculation — only what your trace evidence proves.

  YOU ARE OPUS 4.7 — same model as the human reviewer. If a human
  could read existing skills and write a 100-line policy.py, so can
  you. Stop being risk-averse on simple skill authoring.

  PRIORITIZE DIAGNOSTIC SKILLS (low-risk, high-value):
    If you ever write "X is unreachable" / "all attempts failed" /
    "skill Y always returns None" in root_cause, ASK YOURSELF:
      "Is there a base skill that would have *measured* this for me
       before failure? If not, propose one NOW."
    Examples of diagnostic skills you should propose without
    hesitation (no sim mutation = harness-safe):
      - probe_ik_workspace(arm, x, y, z_range, approaches) — grid IK
        feasibility across wrist orientations
      - probe_contact_envelope(actor, arm) — check which annotated
        contact points are reachable for given arm
      - measure_actor_height(actor) — return actor bbox z-extent
      - simulate_grasp(actor, quat, dry_run=true) — would this grasp
        close on the actor without executing it?
    These are SHORT (50-150 line policy.py), copy structure from
    e.g. base/robotwin/is_reachable. Propose them. Get them in.

  Mode A — TASK SUCCEEDED:
    Consider packaging the successful trace as a NEW COMPOUND SKILL
    so the next LH run can call it directly instead of re-deriving.
    Set proposal_decision = NEW_COMPOUND with proposal_payload.new_code
    reproducing this trace's tool sequence + skill_md (YAML + harness
    block).

  Mode B — TASK FAILED:
    Diagnose WHICH base skill is missing or broken (Read the skill dir
    to confirm):
      - Missing primitive (e.g. "no skill can grasp a bowl rim at
        z<0.84"): confirm by reading base/robotwin/ that nothing covers
        it → NEW_SKILL with full implementation modelled on the closest
        analog.
      - Missing DIAGNOSTIC (e.g. "I claimed unreachable but didn't
        actually probe alternative wrists"): NEW_SKILL — a probe_* skill
        (see list above).
      - Existing skill broken (e.g. "pick_actor_by_contact_point
        returns None target_pose for thin-rim actors"): Read its
        policy.py → SKILL_UPDATE with full replacement policy.py.
    Either way: emit COMPLETE code in proposal_payload (no TODO, no
    diffs). The apply path runs the harness — bad code fails harness,
    not production sim.

CRITICAL EVIDENCE HIERARCHY (per V7/V8 incidents where Engineer
claimed success without proof):
  1. The AUTHORITATIVE SIM GROUND TRUTH block below (actor xyz, gripper
     vals) is the HIGHEST authority — judge EVERY success criterion
     against it. If an actor's z is below its expected lift/place
     height, it is NOT where the Engineer claims — verdict=retry.
  2. The Engineer TOOL TRACE (each tool called + its own ok/result) is
     second — it shows what was attempted and whether each tool itself
     reported success.
  3. Engineer's stdout / done(success=True) is the LOWEST authority.
     Its checks use loose heuristics (gripper near actor centroid) that
     can't distinguish "wrapped around in air" from "still on the
     table". NEVER accept an Engineer success claim the ground truth
     does not confirm.
- proposal_decision defaults to NO_PROPOSAL. Only propose when you can
  point at a SPECIFIC skill .py bug or a missing reusable primitive.
- DO NOT propose if the failure is at LH-executor / planner level
  (atomic ordering / state drift / replanning) — those aren't skill bugs.
- Same skill should not be proposed twice in one LH attempt. The
  caller tracks {already_proposed} — skip skills whose name is in that
  list this turn.

CRITICAL — new_code / skill_md MUST be COMPLETE WORKING CONTENT:

  `new_code` is written VERBATIM to policy.py. It MUST be a complete
  valid Python file that fully replaces the target skill — NOT a diff,
  NOT a TODO comment, NOT prose describing your intended change.

  GOOD (apply will succeed):
    "from __future__ import annotations\\n\\ndef dispatch_runtime(state, args):\\n    # ... ENTIRE working file ...\\n"

  BAD (REJECTED on sight, see V5 proposal 1781109582 rejection):
    "# Add: fallback to mesh rim sampling when contact_point is None"
    "TODO handle dual-arm case"
    "Patch line 87 to check target_pose != None"

  If you do not have a complete working implementation in hand, set
  proposal_decision = NO_PROPOSAL and put your fix sketch in
  next_action. A vague proposal wipes the existing skill and is worse
  than no proposal.

  `skill_md` follows the same rule (complete YAML frontmatter + body,
  not a list of changes you'd like).
"""


_REVIEWER_SYS = (
    "You are the RoboRSI Reviewer — ONE persistent Claude Code session per "
    "task, resumed for every review (per-attempt AND final), so you accumulate "
    "judgment memory across the whole task's history. Judge ONLY from the "
    "authoritative sim ground truth (actor positions) and the Engineer tool "
    "trace given to you in the prompt; never rubber-stamp the Engineer's stdout "
    "claims. You have NO special skill tools — use your own Read/Grep on "
    "roborsi/embodied/skills/ to inspect a skill's code before diagnosing or "
    "proposing. To propose a skill change, put it in your verdict's "
    "proposal_decision + proposal_payload fields; the framework routes it. "
    "Always finish by outputting ONLY the verdict JSON object, nothing else."
)


_FINAL_REVIEWER_PROMPT = """You are the FINAL REVIEWER of a long-horizon
robot manipulation task. The Engineer + per-atomic Reviewer have
finished. Now you look at the whole trajectory and decide:

  1) Did the LH task overall succeed?
  2) If anything went wrong, was there a SYSTEMATIC base-skill issue
     worth proposing a fix for? Or just policy/strategy?

Output ONE JSON object (no fences, no prose):

{
  "lh_verdict": "done" | "partial" | "blocked",
  "root_cause": "≤150 chars",
  "next_action": "concrete recommendation",
  "proposal_decision": "NO_PROPOSAL" | "SKILL_UPDATE" | "NEW_SKILL" | "NEW_COMPOUND",
  "proposal_payload": {
    "name": "...", "kind": "update"|"new", "category": "base/robotwin",
    "rationale": "...", "new_code": "...", "skill_md": "..."
  },
  "review_md": "≤400 chars markdown for human"
}

If the failure was at the LH-EXECUTOR or PLANNER level (wrong atomic
ordering / state drift) emit NO_PROPOSAL — that's not a skill bug.

CRITICAL — proposal_payload.new_code / skill_md format:

  `new_code` is written VERBATIM to policy.py. MUST be a COMPLETE valid
  Python file — NOT a diff, NOT a TODO comment, NOT prose. The apply
  path will overwrite the existing skill file with this string.

  GOOD: "from __future__ import annotations\\n\\ndef dispatch_runtime...\\n    # full working file\\n"
  BAD:  "# Add: fallback when target_pose is None" (this wipes the existing skill!)

  If you don't have a complete implementation ready, emit
  proposal_decision = NO_PROPOSAL and put the fix sketch in next_action.

  Same rule for `skill_md` (complete YAML+markdown, not a change list).

  V5 LH proposal `1781109582-update-pick_actor_by_contact_point-f0d52c`
  was REJECTED for exactly this: new_code was a one-line TODO comment.
  Don't repeat it.
"""


def _serialize_trace(trace: list) -> str:
    """Compact text of the Engineer's tool sequence (the persistent Reviewer
    session can't see the in-memory conversation, so we pass the trace)."""
    import ast
    rows: list[str] = []
    for i, tev in enumerate(trace or []):
        tc = tev.get("tool_call")
        if isinstance(tc, str):
            try:
                tc = ast.literal_eval(tc)
            except (ValueError, SyntaxError):
                tc = {}
        tool = tc.get("tool", "?") if isinstance(tc, dict) else "?"
        args = tc.get("args", {}) if isinstance(tc, dict) else {}
        res = tev.get("result") or tev.get("result_preview") or tev.get("ok") or ""
        rows.append(f"{i + 1}. {tool}({str(args)[:120]}) -> {str(res)[:160]}")
    return "\n".join(rows) or "(no tool trace)"


def _parse_verdict(text: str) -> dict:
    """Extract the verdict JSON object from the reviewer session's reply."""
    out = {"verdict": "retry", "root_cause": "review parse failed",
           "next_action": "", "evidence": "",
           "proposal_decision": "NO_PROPOSAL", "proposal_payload": {}}
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            out.update(json.loads(m.group(0)))
        except json.JSONDecodeError:
            pass
    return out


class Reviewer:
    """Reads workspace + trace + gate_log; writes review.md; optional propose."""

    DEFAULT_MODEL = "anthropic/claude-opus-4-8"

    def __init__(self, model: str | None = None,
                 filter_mode: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        # FILTER_MODE controls what happens when a proposal is emitted.
        # "human" (default): drop into skill_review/ for human approval +
        #   generate an HTML diff page.
        # "auto": let Planner run harness + similarity validation; auto-apply.
        self.filter_mode = (filter_mode
                            or os.environ.get("ROBORSI_FILTER_MODE", "human")
                            ).lower()

    def review(self, *, workspace: Workspace,
                engineer_result: dict[str, Any],
                run_id: str | None = None,
                ns: str = "robotwin") -> dict[str, Any]:
        """Run the Reviewer LLM, write review.md, optionally drop proposal.

        `ns` = the active backend's skill namespace. It steers where a proposed
        skill lands (base/<ns>/, not always robotwin) and swaps the namespace
        mentions in the Reviewer prompt so a libero run proposes a libero skill."""
        self._active_ns = ns
        plan_md = workspace.read_plan()
        summary_md = workspace.read_summary()
        trace = engineer_result.get("trace") or []
        gate_log = _gate_log_for_run(run_id) if run_id else []

        user_block = (
            f"=== plan.md ===\n{plan_md}\n\n"
            f"=== summary.md ===\n{summary_md}\n\n"
            f"=== ENGINEER RESULT ===\n"
            f"success={engineer_result.get('success')} "
            f"outcome={engineer_result.get('outcome')} "
            f"tool_calls={engineer_result.get('tool_calls')}\n\n"
            f"=== PRIOR HISTORY (this task, across runs — you are a SESSION) ===\n"
            f"{_task_history_block(workspace.task)}\n\n"
            f"=== TOOL RELIABILITY (this run — objective tally) ===\n"
            f"{_tool_reliability_summary(trace)}\n\n"
            f"=== MISSING-SKILL SIGNAL (primitive churn) ===\n"
            f"{_missing_skill_signal(trace)}\n\n"
            f"=== INNER TRACE (first 30 steps) ===\n"
            f"{json.dumps(_sanitize_trace(trace[:30]), default=str)[:6000]}\n\n"
            f"=== gate_log for this run ===\n"
            f"{json.dumps(gate_log, default=str)[:1500] or '(none)'}\n"
        )
        system_prompt = (_SYSTEM_PROMPT if ns == "robotwin"
                         else _SYSTEM_PROMPT.replace("base/robotwin", f"base/{ns}"))
        # Reviewer runs as a PERSISTENT (role=reviewer, task) session — the same
        # session resumes across this task's runs, so it remembers its own prior
        # verdicts (the PRIOR HISTORY block above is the durable wiki slice that
        # survives session rolls). ROBORSI_ROLE_SESSION=0 → stateless one-shot.
        from roborsi.agents import persistent_agent
        content = persistent_agent.run_role(
            "reviewer", workspace.task, user_block,
            system_prompt=system_prompt, model=self.model)

        review = _parse_review_json(content)
        review.setdefault("verdict", "blocked")
        review.setdefault("root_cause", "(reviewer parse failed)")
        review.setdefault("next_action", "")
        review.setdefault("proposal_decision", "NO_PROPOSAL")
        review.setdefault("review_md", "(no review body)")

        # ── Write review.md ──
        review_body = (
            f"# Review · {workspace.task}\n\n"
            f"**Verdict**: `{review['verdict']}`\n"
            f"**Root cause**: {review['root_cause']}\n"
            f"**Next action**: {review['next_action']}\n"
            f"**Proposal decision**: `{review['proposal_decision']}`\n"
            f"**Filter mode**: `{self.filter_mode}`\n\n"
            f"## Details\n{review['review_md']}\n"
        )
        workspace.write_review(review_body)

        # ── Optional proposal output ──
        if review["proposal_decision"] != "NO_PROPOSAL":
            payload = review.get("proposal_payload") or {}
            pid = self._drop_proposal(payload, review, workspace)
            workspace.link_proposal(pid)
            review["proposal_id"] = pid
            # Filter routing
            if self.filter_mode == "human":
                html_path = self._render_html_diff(payload, review, workspace, pid)
                review["html_review_path"] = str(html_path)
                # Rebuild index so the operator sees the new entry.
                try:
                    from roborsi.agents.html_review import build_index_page
                    review["html_index_path"] = str(build_index_page())
                except Exception:
                    pass
            elif self.filter_mode == "auto":
                # Planner.validate_proposal → harness + similarity gate.
                # If both PASS, auto-apply via existing apply path.
                try:
                    from roborsi.agents.validator import ProposalValidator
                    proposal_full = dict(payload)
                    proposal_full["id"] = pid
                    rep = ProposalValidator().validate(proposal_full)
                    review["validation_report"] = rep.to_dict()
                    self._attach_validation_to_skill_review(pid, rep.to_dict())
                    if rep.overall_pass:
                        ok, msg = self._auto_apply(pid)
                        review["auto_apply_status"] = (
                            "applied" if ok else f"failed: {msg}")
                    else:
                        review["auto_apply_status"] = "skipped: " + rep.note
                except Exception as e:
                    review["auto_apply_status"] = (
                        f"validator crashed: {type(e).__name__}: {e}")

        return review

    # ──────────────────────────────────────────────────────────────────
    # Long-horizon review methods (same persistent reviewer session)
    # ──────────────────────────────────────────────────────────────────
    def review_sub_atomic(self, *, task: str, atomic: str, idx: int,
                          attempt_n: int, criteria: list[str], gt_state: str,
                          prior_block: str, trace_text: str,
                          already_proposed: set[str] | None = None) -> dict:
        """Per-attempt LH review via the ONE persistent reviewer session for
        `task` (resumed each attempt). Returns the verdict dict; proposals ride
        the verdict's proposal_payload, routed by the LHExecutor caller.

        Judges from the AUTHORITATIVE sim ground-truth state (the real actor
        positions — not the Engineer's stdout claims) plus the tool trace. (The
        multi-camera frames are still snapshotted to the attempt dir for human/
        Manager inspection, but the session judges from the exact GT, which is
        more reliable than re-reading JPGs and keeps the review fast.)"""
        from roborsi.agents import persistent_agent
        body = _SUB_REVIEWER_PROMPT.format(
            idx=idx, atomic=atomic,
            criteria="\n".join(f"  - {c}" for c in criteria) or "  (none specified)",
            already_proposed=sorted(already_proposed or set()),
        )
        prompt = (
            f"{body}\n\n=== GROUND TRUTH (authoritative sim actor positions — the "
            f"real scene state, NOT the Engineer's claims; judge from this) ===\n"
            f"{gt_state}\n{prior_block}\n\n"
            f"=== ENGINEER TOOL TRACE (attempt #{attempt_n}) ===\n{trace_text}\n\n"
            "Output ONLY the verdict JSON object.")
        return _parse_verdict(persistent_agent.run(
            "reviewer", task, prompt, system_prompt=_REVIEWER_SYS))

    def review_lh(self, *, workspace: Workspace, lh_result: Any) -> dict[str, Any]:
        """Final overall LH review. Reads lh_plan.md + lh_summary.md + each
        per-atomic review.md, decides the overall LH verdict + optional
        skill proposal, writes lh_review.md. Runs through the SAME persistent
        (role=reviewer, task) session as the per-attempt reviews — it already
        remembers every attempt's judgment. There is no separate LHReviewer."""
        lh_plan = (workspace.root / "lh_plan.md").read_text(encoding="utf-8") \
            if (workspace.root / "lh_plan.md").exists() else ""
        lh_summary = (workspace.root / "lh_summary.md").read_text(encoding="utf-8") \
            if (workspace.root / "lh_summary.md").exists() else ""

        per_atomic_reviews = []
        for sub in sorted(workspace.root.glob("[0-9][0-9]_*")):
            review_path = sub / "review.md"
            if review_path.exists():
                per_atomic_reviews.append(
                    f"=== {sub.name} ===\n{review_path.read_text(encoding='utf-8')}"
                )

        mid_block = ""
        if lh_result.mid_proposals:
            already = sorted({mp.get("proposal_id", "?")
                              for mp in lh_result.mid_proposals})
            mid_block = (
                "\n\n=== ALREADY-QUEUED MID-LH PROPOSALS (do NOT duplicate) ===\n"
                + "\n".join(f"  - {p}" for p in already)
            )

        user_block = (
            f"=== LH PLAN ===\n{lh_plan}\n\n"
            f"=== LH SUMMARY ===\n{lh_summary}\n\n"
            f"=== PER-ATOMIC REVIEWS ===\n"
            f"{chr(10).join(per_atomic_reviews) or '(none)'}\n\n"
            f"=== EXECUTOR RESULT ===\n"
            f"success={lh_result.success} "
            f"completed={lh_result.completed_atomics}/{lh_result.total_atomics} "
            f"notes={lh_result.notes!r}"
            f"{mid_block}\n"
        )
        from roborsi.agents import persistent_agent
        final_prompt = (
            f"{_FINAL_REVIEWER_PROMPT}\n\n=== THIS LH RUN ===\n{user_block}\n\n"
            "Output ONLY the final-review JSON object.")
        content = persistent_agent.run("reviewer", workspace.task, final_prompt,
                                       system_prompt=_REVIEWER_SYS)
        review = {"lh_verdict": "blocked", "root_cause": "(parse failed)",
                  "next_action": "", "proposal_decision": "NO_PROPOSAL",
                  "review_md": content[:400]}
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                review.update(json.loads(m.group(0)))
            except json.JSONDecodeError:
                pass

        # Write lh_review.md
        (workspace.root / "lh_review.md").write_text(
            f"# LH Review · {workspace.task}\n\n"
            f"**Verdict**: `{review['lh_verdict']}`\n"
            f"**Root cause**: {review['root_cause']}\n"
            f"**Next action**: {review['next_action']}\n"
            f"**Proposal decision**: `{review['proposal_decision']}`\n"
            f"**Filter mode**: `{self.filter_mode}`\n\n"
            f"## Details\n{review.get('review_md','')}\n",
            encoding="utf-8")

        # If a proposal was emitted, route through the SAME skill_review
        # pipeline (skill_review/<pid>.json + HTML / auto-validate) via this
        # instance's helpers.
        if review["proposal_decision"] != "NO_PROPOSAL":
            payload = review.get("proposal_payload") or {}
            pid = self._drop_proposal(payload, review, workspace)
            workspace.link_proposal(pid)
            review["proposal_id"] = pid
            if self.filter_mode == "human":
                html = self._render_html_diff(payload, review, workspace, pid)
                review["html_review_path"] = str(html)
                try:
                    from roborsi.agents.html_review import build_index_page
                    review["html_index_path"] = str(build_index_page())
                except Exception:
                    pass
            elif self.filter_mode == "auto":
                try:
                    from roborsi.agents.validator import ProposalValidator
                    proposal_full = dict(payload)
                    proposal_full["id"] = pid
                    rep = ProposalValidator().validate(proposal_full)
                    review["validation_report"] = rep.to_dict()
                    self._attach_validation_to_skill_review(pid, rep.to_dict())
                    if rep.overall_pass:
                        ok, msg = self._auto_apply(pid)
                        review["auto_apply_status"] = (
                            "applied" if ok else f"failed: {msg}")
                    else:
                        review["auto_apply_status"] = "skipped: " + rep.note
                except Exception as e:
                    review["auto_apply_status"] = (
                        f"validator crashed: {type(e).__name__}: {e}")

        return review

    # ──────────────────────────────────────────────────────────────────
    def _drop_proposal(self, payload: dict, review: dict,
                        workspace: Workspace) -> str:
        """Write a skill_review/<pid>.json in the existing propose format
        so scripts/apply_selfevo_proposal.py picks it up unchanged."""
        ts = int(time.time())
        name = payload.get("name", "unnamed")
        kind = payload.get("kind", "update")
        suffix = uuid.uuid4().hex[:6]
        pid = f"{ts}-{kind}-{name}-{suffix}"
        record = {
            "id": pid,
            "kind": kind,
            "name": name,
            "category": (f"base/{self._active_ns}"
                         if getattr(self, "_active_ns", "robotwin") != "robotwin"
                         else payload.get("category", "base/robotwin")),
            "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "submitted_by": f"reviewer_agent[{workspace.task}]",
            "status": "pending",
            "new_code": payload.get("new_code", ""),
            "skill_md": payload.get("skill_md", ""),
            "target_path": payload.get("target_path", ""),
            "old_string": payload.get("old_string", ""),
            "new_string": payload.get("new_string", ""),
            "rationale": payload.get("rationale", review.get("review_md", "")),
            "source_workspace": str(workspace.root),
            "verdict": review.get("verdict"),
            "next_action": review.get("next_action"),
        }
        out_dir = Path.home() / ".roborsi" / "skill_review"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{pid}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return pid

    def _attach_validation_to_skill_review(self, pid: str,
                                             report: dict) -> None:
        """Patch the skill_review/<pid>.json with validation_report so
        downstream apply / HTML / human review can see verdicts."""
        path = Path.home() / ".roborsi" / "skill_review" / f"{pid}.json"
        if not path.exists():
            return
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        rec["validation_report"] = report
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    def _auto_apply(self, pid: str) -> tuple[bool, str]:
        """Auto-apply a validated proposal via apply_selfevo_proposal.py.
        Returns (ok, message). Uses --skip-harness since we just ran the
        gate ourselves in ProposalValidator; the apply path's gate would
        re-run it which wastes a sim startup."""
        import subprocess
        repo = Path(__file__).resolve().parents[2]
        cmd = ["python3", "scripts/apply_selfevo_proposal.py", pid,
               "--skip-harness"]
        res = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=str(repo), env=os.environ.copy(), timeout=120,
                                encoding="utf-8", errors="replace")
        ok = res.returncode == 0
        msg = (res.stdout + res.stderr)[-400:].strip()
        return ok, msg

    def _render_html_diff(self, payload: dict, review: dict,
                           workspace: Workspace, pid: str) -> Path:
        """Generate an HTML page for human approval.
        Shows: before/after diff · source workspace · rationale · review verdict.
        Lands in ~/.roborsi/proposal_html/<pid>.html so the operator can
        eyeball it before approving via apply_selfevo_proposal.py."""
        import html
        name = payload.get("name", "?")
        kind = payload.get("kind", "?")
        rationale = html.escape(payload.get("rationale") or "")
        new_code = html.escape(payload.get("new_code") or "")
        skill_md = html.escape(payload.get("skill_md") or "")
        old_code = ""
        if kind == "update":
            from roborsi.embodied.skills import get as get_skill
            sk = get_skill(name)
            if sk is not None:
                pol = sk.path.parent / "policy.py"
                if pol.exists():
                    old_code = html.escape(pol.read_text(encoding="utf-8"))
        body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Skill Proposal · {html.escape(pid)}</title>
<style>
  body{{font-family:-apple-system,sans-serif;max-width:1100px;margin:32px auto;padding:0 24px;color:#222}}
  h1{{font-size:22px;border-bottom:1px solid #ddd;padding-bottom:8px}}
  h2{{font-size:14px;letter-spacing:.1em;text-transform:uppercase;color:#888;margin-top:32px}}
  pre{{background:#f6f8fa;padding:14px;border-radius:4px;font-size:12px;line-height:1.5;overflow:auto;max-height:480px}}
  .meta{{background:#fafafa;border-left:3px solid #888;padding:10px 16px;font-size:13px}}
  .actions{{margin:24px 0;padding:16px;background:#fff8e1;border:1px solid #f0c66e;border-radius:4px;font-family:monospace;font-size:13px}}
</style></head><body>
<h1>{html.escape(kind.upper())} · {html.escape(name)}</h1>
<div class="meta">
  <b>Proposal id:</b> {html.escape(pid)}<br>
  <b>Source:</b> {html.escape(str(workspace.root))}<br>
  <b>Verdict:</b> {html.escape(review.get("verdict", ""))} ·
  <b>Decision:</b> {html.escape(review.get("proposal_decision", ""))}<br>
  <b>Next action:</b> {html.escape(review.get("next_action", ""))}
</div>

<h2>Rationale (Source)</h2>
<pre>{rationale}</pre>

{"<h2>New SKILL.md</h2><pre>" + skill_md + "</pre>" if skill_md else ""}

{("<h2>Before · current policy.py</h2><pre>" + old_code + "</pre>") if old_code else ""}

<h2>{("After · proposed policy.py" if old_code else "New policy.py")}</h2>
<pre>{new_code}</pre>

<div class="actions">
APPROVE:  python3 scripts/apply_selfevo_proposal.py {html.escape(pid)} --skip-harness<br>
REJECT:   python3 scripts/apply_selfevo_proposal.py --reject {html.escape(pid)}
</div>

</body></html>
"""
        out_dir = Path.home() / ".roborsi" / "proposal_html"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{pid}.html"
        path.write_text(body, encoding="utf-8")
        return path
