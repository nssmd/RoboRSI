"""LHExecutor — orchestrates a long-horizon task with sustained agents.

Per user 2026-06-09 LH design call:

  - Planner.decompose runs ONCE up front (already done in workspace).
  - For each ordered atomic:
      while True (capped):
          Engineer continues sustained session, drives sim for THIS atomic.
          Reviewer judges JUST-completed atomic from inside the same session.
          if Reviewer says done → break, advance to next atomic.
          if Reviewer says retry → append feedback as user msg, re-run sim
                                    (same env, same conversation).
  - After all atomics pass: Reviewer.review_lh writes lh_review.md + optional propose.

Shared context: Engineer's `messages` list survives across atomic calls.
The same LLM session sees: atomic_0 trace → reviewer feedback → atomic_0
retry → ... → success → atomic_1 instruction → ... etc. No cold starts.

Workspace layout the executor produces:

  workspace/<lh_task>-<rid>/
  ├── lh_plan.md                  (Planner.decompose)
  ├── lh_summary.md               (this module — collects per-atomic outcomes)
  ├── lh_review.md                (Reviewer.review_lh at end)
  ├── 00_<atomic>/
  │   ├── plan.md                 (Planner.decompose — already there)
  │   ├── attempt_1/rollout/      (sim frames)
  │   ├── attempt_1/result.json
  │   ├── attempt_2/...
  │   └── review.md               (per-atomic Reviewer verdict, last attempt)
  └── 01_<atomic>/
      └── ...
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roborsi.agents.workspace import Workspace
from roborsi.agents.skill_history import record_success
from roborsi.agents.plan_archive import archive_successful_plan
from roborsi.agents.atomic_bottleneck import (
    record_atomic_outcome, mark_resolved as bn_mark_resolved,
)
from roborsi.agents.lh_sim_state import (
    snapshot_scene, restore_scene, ground_truth_state,
    snapshot_review_frames, sim_check_success,
)


_ENGINEER_MODEL = "anthropic/claude-opus-4-8"
_REVIEWER_MODEL = "anthropic/claude-opus-4-8"

# Cap retries per atomic so we don't infinite-loop a hopeless atomic.
MAX_ATOMIC_RETRIES = 4
# Higher cap when the LH Planner (Planner.decompose) is running in FOCUS
# mode (single bottlenecked atomic). Justifies more retries because there's
# no waste cascading to downstream atomics — all energy goes into one stuck
# step.
MAX_ATOMIC_RETRIES_FOCUS = 10


@dataclass
class AtomicAttemptResult:
    atomic: str
    index: int
    attempt: int
    success: bool
    outcome: str
    tool_calls: int
    review_verdict: str = ""
    review_root_cause: str = ""
    review_next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class LHExecutorResult:
    lh_task: str
    success: bool
    completed_atomics: int
    total_atomics: int
    attempts: list[AtomicAttemptResult] = field(default_factory=list)
    notes: str = ""
    mid_proposals: list[dict] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Per-atomic + final LH reviewing live on the shared Reviewer class
# (agents/reviewer.py): Reviewer.review_sub_atomic (per-attempt) and
# Reviewer.review_lh (final). There is no separate LHReviewer.
# ──────────────────────────────────────────────────────────────────────────


def _route_mid_proposal(*, review: dict, workspace: Workspace,
                          atomic: str, idx: int, attempt_n: int,
                          filter_mode: str,
                          model: str) -> dict:
    """Route a mid-atomic proposal through the same skill_review pipeline
    used by Reviewer.review (atomic) + Reviewer.review_lh (final LH):
      - drop into ~/.roborsi/skill_review/<pid>.json (same shape)
      - human filter: render per-proposal HTML diff page + rebuild index
      - auto filter: ProposalValidator → auto-apply on PASS

    Returns a dict with proposal_id + status info that LHExecutor logs.
    """
    from roborsi.agents.reviewer import Reviewer as AtomicReviewer
    payload = review.get("proposal_payload") or {}
    if not payload.get("name"):
        return {}
    # Reuse AtomicReviewer's drop + render + auto helpers.
    ar = AtomicReviewer(model=model, filter_mode=filter_mode)
    pid = ar._drop_proposal(payload, review, workspace)
    workspace.link_proposal(pid)
    out: dict[str, Any] = {
        "proposal_id": pid,
        "from": f"sub_review · atomic_{idx} ({atomic}) attempt {attempt_n}",
        "filter_mode": filter_mode,
    }
    if filter_mode == "human":
        try:
            html = ar._render_html_diff(payload, review, workspace, pid)
            out["html_review_path"] = str(html)
        except Exception as e:
            out["html_error"] = f"{type(e).__name__}: {e}"
        try:
            from roborsi.agents.html_review import build_index_page
            out["html_index_path"] = str(build_index_page())
        except Exception:
            pass
    elif filter_mode == "auto":
        try:
            from roborsi.agents.validator import ProposalValidator
            proposal_full = dict(payload)
            proposal_full["id"] = pid
            rep = ProposalValidator().validate(proposal_full)
            out["validation_report"] = rep.to_dict()
            ar._attach_validation_to_skill_review(pid, rep.to_dict())
            if rep.overall_pass:
                ok, msg = ar._auto_apply(pid)
                out["auto_apply_status"] = (
                    "applied" if ok else f"failed: {msg}"
                )
            else:
                out["auto_apply_status"] = "skipped: " + rep.note
        except Exception as e:
            out["auto_apply_status"] = (
                f"validator crashed: {type(e).__name__}: {e}"
            )
    return out


# ──────────────────────────────────────────────────────────────────────────
# Top-level executor
# ──────────────────────────────────────────────────────────────────────────


class LHExecutor:
    """Drives the full LH pipeline. Caller provides workspace already
    populated by Planner.decompose (lh_plan.md + per-atomic plan.md)."""

    def __init__(self, engineer_model: str | None = None,
                 reviewer_model: str | None = None,
                 backend_name: str | None = None) -> None:
        self.engineer_model = engineer_model or _ENGINEER_MODEL
        self.reviewer_model = reviewer_model or _REVIEWER_MODEL
        self.backend_name = backend_name  # None → auto-detect from task

    def _resolve_backend(self, task: str) -> str:
        if self.backend_name:
            return self.backend_name
        # Heuristic: BiCoord-Bench tasks end in `_bicoord` (e.g.
        # handover_block_bicoord) and require backend='bicoord' which
        # points at /data/.../BiCoord-Bench. Everything else assumes the
        # default RoboTwin backend.
        return "bicoord" if task.endswith("_bicoord") else "robotwin"

    def _resolve_sim_task(self, lh_task: str) -> str:
        """LH task name (handover_block_bicoord) → sim env name
        (handover_block_with_bowls). Reads `sim_task` from the LH
        skill's SKILL.md frontmatter; falls back to lh_task itself."""
        from roborsi.embodied.skills import get as get_skill
        sk = get_skill(lh_task)
        if sk and sk.frontmatter:
            sim = sk.frontmatter.get("sim_task")
            if isinstance(sim, str) and sim.strip():
                return sim.strip()
            meta = sk.frontmatter.get("metadata") or {}
            if isinstance(meta, dict):
                sim = meta.get("sim_task")
                if isinstance(sim, str) and sim.strip():
                    return sim.strip()
        return lh_task

    def execute(self, *, mission_spec: dict[str, Any],
                 workspace: Workspace,
                 seed: int) -> LHExecutorResult:
        from roborsi.embodied.agent_loop import get_backend
        from roborsi.embodied.agent_loop.rollout import run_rollout

        ordered = mission_spec.get("ordered_atomics") or []
        focus_mode = bool(mission_spec.get("focus_mode"))
        retry_cap = MAX_ATOMIC_RETRIES_FOCUS if focus_mode else MAX_ATOMIC_RETRIES
        result = LHExecutorResult(
            lh_task=workspace.task,
            success=False,
            completed_atomics=0,
            total_atomics=len(ordered),
        )
        if focus_mode:
            result.notes = f"FOCUS mode · retry cap={retry_cap}"
        if not ordered:
            result.notes = "Planner.decompose produced empty ordered_atomics."
            return result

        # ── Open backend env ONCE for the whole LH (env state persists
        #    across atomics — this is the point of LH). ──
        backend_name = self._resolve_backend(workspace.task)
        backend = get_backend(backend_name)
        ok, reason = backend.available()
        if not ok:
            result.notes = f"backend '{backend_name}' unavailable: {reason}"
            return result
        sim_task = self._resolve_sim_task(workspace.task)

        # Wire live_trace so rollout's emit_inner per-step calls land in
        # trace.db (the steps table). Without this, sim runs but every
        # inner_tool_call event silently no-ops (emit_inner requires
        # set_inner_target). Operator can then watch live with
        # scripts/watch_lh_trace.py.
        from roborsi.channels.agent.feishu import live_trace as _lt
        from roborsi.store import trace_db as _td
        live_chat_id = f"lh3role-{workspace.task}-{workspace.run_id}"
        live_sess = _lt.get_session(live_chat_id)
        _lt.set_inner_target(live_sess)

        # Each atomic gets its OWN fresh Engineer session (per user
        # 2026-06-10: "每个 atomic 应该是 engineer 不同的"). Within an
        # atomic, retries reuse the same messages so retry knows what
        # the prior attempt did. Across atomics, ALWAYS cold-start so
        # atomic_1's Engineer doesn't carry atomic_0's bloated history.
        engineer_started = False

        # Track skill names already proposed in this LH run so per-atomic
        # Reviewers don't double-propose for the same target.
        already_proposed: set[str] = set()
        import os
        filter_mode = os.environ.get("ROBORSI_FILTER_MODE", "human").lower()
        mid_proposals: list[dict] = []

        with backend.make_env(sim_task, {"require_depth": True}) as env:
            env.reset(seed)
            # Per-atomic end-state snapshots so a stuck downstream atomic
            # can rollback to a prior atomic's exit state and have the
            # earlier atomics re-run (which may leave a more favorable
            # state for the stuck step). Indexed by ordered_atomics list
            # position. Per 2026-06-15 user request: "atomic 失败回退到
            # 任意 K 重做" — env is in sim, so cheap to do this.
            post_atomic_snapshots: dict[int, Any] = {}
            rollbacks_used = 0
            MAX_ROLLBACKS = 2
            i = 0
            while i < len(ordered):
                entry = ordered[i]
                idx = int(entry.get("index", 0))
                atomic = entry.get("atomic", f"step_{idx}")
                sub_dir = workspace.root / f"{idx:02d}_{atomic}"
                sub_plan_md = (sub_dir / "plan.md").read_text(encoding="utf-8") \
                    if (sub_dir / "plan.md").exists() else f"# Atomic {idx}: {atomic}\n"
                criteria = entry.get("success_criteria") or []
                criteria_str = "; ".join(criteria) or "atomic-specific success"

                last_attempt: AtomicAttemptResult | None = None
                # Fresh Engineer session per atomic (cold start). Within this
                # atomic, retries share `engineer_messages` so retry sees
                # prior attempt's tool calls + reviewer feedback. The next
                # atomic re-enters with engineer_messages=None.
                engineer_messages: list[dict] | None = None
                # Track whether the Reviewer amended an immutable plan section
                # for THIS atomic — the LH analog of a mid-run plan() revision.
                # Fed to _enqueue_plan_promotion so a promoted plan carries the
                # revision provenance the Manager weighs before overwriting the
                # atomic's seed.
                atomic_replanned = False
                atomic_revision_reason = ""
                # Snapshot sim state BEFORE this atomic — restored before
                # each retry so prior fail's flailing arm doesn't pollute
                # the next attempt's starting state (sub-Reviewer caught
                # this 2026-06-09: 'env corruption' was its diagnosis).
                pre_atomic_snapshot = snapshot_scene(env)

                for attempt_n in range(1, retry_cap + 1):
                    attempt_dir = sub_dir / f"attempt_{attempt_n}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)

                    # Per-attempt run_id so each attempt's inner trace lands
                    # in trace.db.steps under its own queryable id — operator
                    # can watch live via scripts/watch_lh_trace.py.
                    attempt_run_id = (
                        f"lh-{workspace.run_id}-a{idx:02d}-{atomic}-att{attempt_n}"
                    )
                    _td.insert_run(attempt_run_id, task=f"{workspace.task}.{atomic}",
                                    skill=f"{atomic}.zeroshot",
                                    seed=seed, chat_id=live_chat_id)
                    _lt.set_inner_run_id(attempt_run_id)
                    live_sess.append("lh_attempt_start",
                                       atomic=atomic, idx=idx, attempt=attempt_n,
                                       run_id=attempt_run_id)

                    # Before a retry, give the Engineer a clean starting
                    # state. For the FIRST atomic (the two-bowl tilt-pour) a
                    # failed pour routinely flings the bowls/block off the
                    # table, and snapshot-restore CANNOT recover a slept
                    # off-scene rigid body (V75/V76: wake_up + extra steps
                    # didn't help, Reviewer flagged "off-scene cup" 3×). So
                    # just REGENERATE the whole scene from seed and let the
                    # Engineer start over from grasping — user 2026-06-17:
                    # "倒方块失败就从抓碗开始". Later atomics restore their
                    # pre-atomic snapshot so earlier progress isn't lost.
                    if attempt_n > 1:
                        if idx == 0:
                            env.reset(seed)
                            pre_atomic_snapshot = snapshot_scene(env)
                        elif pre_atomic_snapshot is not None:
                            restore_scene(env, pre_atomic_snapshot)

                    # If retrying, preceding review_sub_atomic already wrote a
                    # user("Reviewer says retry: ...") into engineer_messages.
                    # Build instruction text — first attempt = full plan,
                    # retries = STRONG retry prompt that forbids early give-up.
                    if attempt_n == 1:
                        from roborsi.agents.task_wiki import read_wiki
                        wiki_md = read_wiki(workspace.task)
                        instruction = (
                            f"GOAL: {entry.get('why', atomic)}\n\n"
                            f"PLAN (this atomic only):\n{sub_plan_md}\n\n"
                            f"SUCCESS CRITERIA: {criteria_str}\n\n"
                            f"━━ TASK WIKI (proven + failed traces, "
                            f"measurements) ━━\n"
                            f"{wiki_md}\n"
                            f"━━ END TASK WIKI ━━\n"
                            f"Use the wiki above as your primary reference: "
                            f"replay successful tool sequences if scene "
                            f"matches; avoid failure modes; trust the "
                            f"measurements (IK floors, attach points) "
                            f"verbatim. Live obs (describe_scene_actors) "
                            f"is still the source of truth for actor xyz "
                            f"— never copy literal xyz from wiki entries."
                        )
                    else:
                        instruction = (
                            f"RETRY attempt #{attempt_n} of atomic_{idx} ({atomic}).\n"
                            f"Sim state has been RESTORED to pre-atomic snapshot "
                            f"(arms reset, actors back where they started). "
                            f"You are NOT inheriting any flailing arm or moved "
                            f"actor from the prior attempt — this is effectively "
                            f"a fresh start with knowledge of what failed.\n\n"
                            f"Reviewer's feedback already injected above. "
                            f"You MUST actually try a different approach this "
                            f"time. Calling done(success=False) within 1-2 tool "
                            f"calls is FORBIDDEN — that's giving up. Work the "
                            f"full tool budget. If you genuinely believe the "
                            f"atomic is impossible with current skills, you "
                            f"may call propose_skill_update / propose_new_skill "
                            f"inside this attempt (mid-LH proposals are queued "
                            f"automatically) instead of bailing."
                        )

                    try:
                        m_result = run_rollout(
                            env, seed=seed,
                            task_name=f"{workspace.task}__{idx}_{atomic}",
                            instruction=instruction,
                            expected_on_success=criteria_str,
                            model=self.engineer_model,
                            tool_budget=40,
                            workdir=attempt_dir / "rollout",
                            prior_messages=engineer_messages,  # sustained!
                            # LH sub-atomic: sim.check_success() is the FULL
                            # task predicate, never True mid-handover — gate
                            # per-atomic success on progress_judge, not sim.
                            use_sim_predicate=False,
                        )
                        engineer_started = True
                        engineer_messages = m_result.messages

                        # Persist this attempt's stats.
                        attempt_record = {
                            "attempt": attempt_n,
                            "outcome": m_result.outcome,
                            "success": bool(m_result.rollout.success),
                            "tool_calls": len(m_result.trace),
                        }
                        (attempt_dir / "result.json").write_text(
                            json.dumps(attempt_record, indent=2,
                                        default=str), encoding="utf-8")
                    except Exception as exc:
                        # sim crash mid-attempt (cuRobo / sapien / bad tool
                        # args) — don't kill the whole LH. Mark this attempt
                        # as failed, inject error text into the sustained
                        # conversation so next retry's Engineer sees it,
                        # and continue the retry loop.
                        err = f"{type(exc).__name__}: {exc}"
                        attempt_record = {
                            "attempt": attempt_n,
                            "outcome": f"crash: {err}",
                            "success": False,
                            "tool_calls": 0,
                            "exception": err,
                        }
                        (attempt_dir / "result.json").write_text(
                            json.dumps(attempt_record, indent=2,
                                        default=str), encoding="utf-8")
                        # Append error to engineer_messages so the next
                        # attempt's Opus call sees what blew up.
                        engineer_messages = engineer_messages or [{
                            "role": "user",
                            "content": (f"Working on {workspace.task} seed={seed}. "
                                        "Drive the sim via base/robotwin tools."),
                        }]
                        engineer_messages.append({
                            "role": "user",
                            "content": (
                                f"PRIOR ATTEMPT CRASHED before completion: "
                                f"`{err}`. This usually means a tool call had "
                                f"invalid args (None, wrong type) or sim "
                                f"refused the move. Read the error type, fix "
                                f"the bad call, retry."
                            ),
                        })
                        # Build a synthetic last_attempt and continue retry.
                        last_attempt = AtomicAttemptResult(
                            atomic=atomic, index=idx, attempt=attempt_n,
                            success=False, outcome=f"crash: {type(exc).__name__}",
                            tool_calls=0,
                            review_verdict="retry",
                            review_root_cause=err[:150],
                            review_next_action="fix bad tool args; the sim's "
                                                "error message names the issue",
                        )
                        result.attempts.append(last_attempt)
                        continue

                    # Reviewer (ONE persistent session per task) judges from
                    # AUTHORITATIVE sim ground truth + the post-attempt camera
                    # frames (which it Reads from disk itself) + the Engineer's
                    # tool trace — NOT the Engineer's stdout claims (V7: Engineer
                    # printed "held: True" while the bowl never lifted).
                    gt_state = ground_truth_state(env)
                    prior_block = self._prior_failures_block(
                        result.attempts, idx, atomic)
                    snapshot_review_frames(env, attempt_dir)  # save jpgs for human inspection
                    from roborsi.agents.reviewer import (
                        Reviewer, _serialize_trace,
                    )
                    review = Reviewer(model=self.reviewer_model).review_sub_atomic(
                        task=workspace.task, atomic=atomic, idx=idx,
                        attempt_n=attempt_n, criteria=criteria,
                        gt_state=gt_state, prior_block=prior_block,
                        trace_text=_serialize_trace(m_result.trace),
                        already_proposed=already_proposed)

                    # Reviewer-driven plan amendment — applied immediately
                    # to plan.md for the next attempt, logged for audit.
                    # Only Reviewer can amend Goal/Hard rules/Done gate/
                    # Success criteria; Engineer can amend Recipe via the
                    # update_recipe tool. (per user 2026-06-11)
                    pa = review.get("plan_amend") or {}
                    if pa.get("section") and pa.get("new_text"):
                        self._apply_plan_amend(sub_dir, pa, attempt_n)
                        atomic_replanned = True
                        atomic_revision_reason = str(pa.get("reason") or "")

                    # Mid-atomic propose — Reviewer can drop a proposal
                    # immediately rather than wait for end-of-LH.
                    pdec = review.get("proposal_decision") or "NO_PROPOSAL"
                    payload = review.get("proposal_payload") or {}
                    target_name = payload.get("name") or ""
                    if (pdec != "NO_PROPOSAL"
                            and target_name
                            and target_name not in already_proposed):
                        routing = _route_mid_proposal(
                            review=review, workspace=workspace,
                            atomic=atomic, idx=idx, attempt_n=attempt_n,
                            filter_mode=filter_mode,
                            model=self.reviewer_model,
                        )
                        if routing.get("proposal_id"):
                            already_proposed.add(target_name)
                            mid_proposals.append(routing)

                    # ── Integrity backstops on atomic success (2026-06-27) ──
                    # The Reviewer verdict alone is NOT authoritative. It
                    # rubber-stamped match_blocks as `done` when the Engineer
                    # had honestly called done(success=False) ("infeasible")
                    # and nothing was placed — a false LH completion. Two hard
                    # code-level backstops the Reviewer's word cannot override:
                    #   (1) Engineer's explicit failure: if the Engineer called
                    #       done(success=False), the atomic is NOT done unless
                    #       the sim predicate positively confirms it.
                    #   (2) Final-atomic sim predicate: the LAST atomic's
                    #       check_success IS the full-LH predicate; if it reads
                    #       False the task is not complete, whatever the
                    #       Reviewer says. Intermediate atomics legitimately
                    #       read False, so the sim backstop gates only the last.
                    reviewer_done = (review.get("verdict") == "done")
                    is_last_atomic = (i == len(ordered) - 1)
                    # Settle before reading the FINAL predicate (gripper finishes
                    # opening, object comes to rest); intermediate atomics read
                    # the full-LH predicate without settling (it is False anyway).
                    sim_ok = sim_check_success(
                        env, settle_ticks=15 if is_last_atomic else 0)
                    engineer_declared_fail = (
                        m_result.outcome == "vlm_declared_done"
                        and not bool(m_result.success))
                    atomic_success = reviewer_done
                    if engineer_declared_fail and not sim_ok:
                        atomic_success = False
                    if is_last_atomic and not sim_ok:
                        atomic_success = False

                    last_attempt = AtomicAttemptResult(
                        atomic=atomic, index=idx, attempt=attempt_n,
                        success=atomic_success,
                        outcome=m_result.outcome,
                        tool_calls=len(m_result.trace),
                        review_verdict=review.get("verdict", ""),
                        review_root_cause=review.get("root_cause", ""),
                        review_next_action=review.get("next_action", ""),
                    )
                    result.attempts.append(last_attempt)

                    # Persist per-atomic review.md (last attempt's view).
                    (sub_dir / "review.md").write_text(
                        f"# Review · atomic_{idx} ({atomic})\n\n"
                        f"**Verdict**: `{review.get('verdict', '?')}`\n"
                        f"**Attempts**: {attempt_n}\n"
                        f"**Root cause**: {review.get('root_cause','')}\n"
                        f"**Next action**: {review.get('next_action','')}\n"
                        f"**Evidence**: {review.get('evidence','')}\n",
                        encoding="utf-8")

                    # Record the trace's tool sequence so we can append it
                    # to the wiki (success or failure).
                    trace_events = []
                    for tev in (m_result.trace or []):
                        tc = tev.get("tool_call")
                        if isinstance(tc, str):
                            import ast as _ast
                            try:
                                tc = _ast.literal_eval(tc)
                            except Exception:
                                tc = {}
                        if isinstance(tc, dict):
                            trace_events.append({
                                "tool": tc.get("tool", "?"),
                                "args": tc.get("args") or {},
                            })

                    if last_attempt.success:
                        # Auto-record success trace to wiki.
                        from roborsi.agents.task_wiki import (
                            append_success_trace, _enqueue_plan_promotion,
                        )
                        append_success_trace(
                            task=workspace.task, atomic=atomic, seed=seed,
                            run_id=workspace.run_id,
                            tool_events=trace_events,
                            tool_calls_total=len(m_result.trace),
                        )
                        # Propose promoting this atomic's just-succeeded plan.md
                        # into the atomic's persistent (read-only) seed — the
                        # SAME plan-promotion the atomic 3-role path enqueues on
                        # success (bot_agent._run_atomic_3role). Manager-gated via
                        # resolve_plan_promotion; a no-op when the plan is
                        # byte-identical to the seed. Keyed by `atomic` so the LH
                        # per-atomic plan feeds the same seed the atomic path uses.
                        sub_plan = sub_dir / "plan.md"
                        _enqueue_plan_promotion(
                            task=atomic, run_id=workspace.run_id,
                            workspace_plan_md=(sub_plan.read_text(encoding="utf-8")
                                               if sub_plan.exists() else ""),
                            rationale=(review.get("root_cause", "")
                                       or m_result.outcome or ""),
                            engineer_replanned=atomic_replanned,
                            reason_for_revision=atomic_revision_reason,
                        )
                        # Snapshot the post-atomic state for potential
                        # rollback from a later stuck atomic.
                        post_atomic_snapshots[i] = snapshot_scene(env)
                        break  # advance to next atomic
                    else:
                        # Auto-record failure trace + Reviewer diagnosis.
                        from roborsi.agents.task_wiki import append_failure_trace
                        append_failure_trace(
                            task=workspace.task, atomic=atomic, seed=seed,
                            run_id=workspace.run_id,
                            tool_events=trace_events,
                            tool_calls_total=len(m_result.trace),
                            reviewer_root_cause=review.get("root_cause", ""),
                            reviewer_next_action=review.get("next_action", ""),
                        )
                    # else: append explicit feedback turn so next loop's
                    # Engineer call sees "you must change X". The
                    # review_sub_atomic already appended its own messages, but
                    # add an explicit imperative for clarity.
                    engineer_messages.append({
                        "role": "user",
                        "content": (
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"⚠ MANDATORY: Reviewer rejected your last "
                            f"attempt on atomic_{idx} ({atomic}). You MUST "
                            f"follow the next_action below VERBATIM on the "
                            f"next attempt. Repeating the failing strategy "
                            f"is forbidden.\n\n"
                            f"REVIEWER ROOT CAUSE: {review.get('root_cause','')}\n\n"
                            f"REVIEWER NEXT ACTION (apply EXACTLY):\n"
                            f"  {review.get('next_action','retry with adjusted parameters')}\n\n"
                            f"If the next_action says 'switch arm', SWITCH "
                            f"THE ARM — do not retry the same arm. If it "
                            f"says 'probe IK first', call probe_ik_workspace "
                            f"as YOUR FIRST tool call. If it names a specific "
                            f"skill to use, use THAT skill before any other.\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                    })

                # Record atomic outcome to bottleneck log so future
                # LH Planner (Planner.decompose) calls can decide whether this
                # atomic deserves FOCUS mode in the next LH attempt.
                atomic_ok = bool(last_attempt and last_attempt.success)
                attempts_used = last_attempt.attempt if last_attempt else 0
                record_atomic_outcome(
                    lh_task=workspace.task, atomic=atomic,
                    success=atomic_ok, attempts_used=attempts_used,
                    source_run_id=workspace.run_id,
                    blocking_skill=atomic if not atomic_ok else "",
                )
                if focus_mode and atomic_ok:
                    bn_mark_resolved(atomic)

                if not last_attempt or not last_attempt.success:
                    # Try ROLLBACK to a prior atomic's post-state and
                    # re-run from there. The earlier atomics may, on
                    # re-run, leave the world in a more favorable state
                    # for the stuck step (e.g. bowl pose / arm pose).
                    # Per 2026-06-15 user request.
                    rollback_target = i - 2
                    if (rollbacks_used < MAX_ROLLBACKS
                            and rollback_target >= 0
                            and rollback_target in post_atomic_snapshots):
                        rollbacks_used += 1
                        restore_scene(env, post_atomic_snapshots[rollback_target])
                        # Drop post-snapshots beyond the rollback target —
                        # those atomics will be re-run and produce new
                        # post-states.
                        for k in list(post_atomic_snapshots.keys()):
                            if k > rollback_target:
                                del post_atomic_snapshots[k]
                        result.notes = (
                            (result.notes + " | " if result.notes else "")
                            + f"rollback #{rollbacks_used}: atomic_{idx} "
                              f"failed → restored to post-atomic_{rollback_target} "
                              f"end-state, re-running from atomic_{rollback_target+1}"
                        )
                        # Reset completed_atomics to reflect actual progress.
                        result.completed_atomics = rollback_target + 1
                        i = rollback_target + 1  # re-enter from there
                        continue
                    result.notes = (
                        (result.notes + " | " if result.notes else "")
                        + f"atomic_{idx} ({atomic}) failed after "
                          f"{retry_cap} retries; rollbacks_used="
                          f"{rollbacks_used}/{MAX_ROLLBACKS} — aborting LH."
                    )
                    break  # fail out of the LH

                result.completed_atomics = i + 1
                i += 1  # advance to next atomic

        result.success = (result.completed_atomics == len(ordered))
        result.mid_proposals = mid_proposals

        # Cleanup live_trace pointers so a subsequent LH doesn't inherit
        # this LH's chat_id / run_id.
        try:
            _lt.set_inner_target(None)
            _lt.set_inner_run_id(None)
        except Exception:
            pass

        # ── Write lh_summary.md ──
        self._write_summary(workspace, result, mission_spec)

        # ── Record success/archive plan if all atomics passed ──
        if result.success:
            all_skills = sorted({
                a.atomic for a in result.attempts if a.success
            })
            record_success(task=workspace.task, skills_used=all_skills)
            lh_plan = (workspace.root / "lh_plan.md").read_text(encoding="utf-8")
            archive_successful_plan(
                task=workspace.task, plan_md=lh_plan,
                skills_used=all_skills,
            )

        return result

    @staticmethod
    def _prior_failures_block(attempts: list, idx: int, atomic: str) -> str:
        """Cross-attempt failure digest for THIS atomic, injected into the
        Reviewer verbatim. The Engineer convo gets summarized at 30 msgs
        (robotwin_agent.SUMMARIZE_AT_MSGS), which silently erases the
        'we already tried tool A, B, C and every one IK-failed' history —
        so the Reviewer, seeing only the latest attempt, keeps diagnosing
        'wrong tool choice' and never escalates to a skill-code fix. This
        block reconstructs that global pattern from result.attempts (which
        is NOT summarized) so the deadlock is visible. Empty on attempt 1.
        """
        prior = [a for a in attempts if getattr(a, "index", None) == idx]
        if not prior:
            return ""
        lines = []
        for a in prior:
            cause = (a.review_root_cause or a.outcome or "?").strip()
            lines.append(f"  • attempt {a.attempt}: {cause}")
        body = "\n".join(lines)
        return (
            f"\n\n━━ CROSS-ATTEMPT FAILURE HISTORY · atomic_{idx} ({atomic}) "
            f"— survives convo summarization, READ BEFORE JUDGING ━━\n"
            f"This atomic has ALREADY failed {len(prior)} time(s):\n{body}\n"
            f"SKILL-DEADLOCK TEST: if ≥2 attempts above hit the SAME failure "
            f"mode (grasp IK infeasible / 300s timeout / object knocked off "
            f"table) — even across DIFFERENT tools — then no further tool "
            f"swap will fix it; that IS a skill-code limitation. You MUST "
            f"then set proposal_decision=SKILL_UPDATE with a COMPLETE fixed "
            f"policy.py in proposal_payload, NOT "
            f"another 'try tool X' next_action. A repeated-mode failure "
            f"answered with NO_PROPOSAL is a review failure on your part.\n━━"
        )

    @staticmethod
    def _apply_plan_amend(sub_dir: Path, amend: dict, attempt_n: int) -> None:
        """Apply Reviewer's plan amendment to the named section of the per-run
        workspace plan (<sub_dir>/plan.md) ONLY. The persistent skill-dir
        plan.md is a READ-ONLY SEED and is NOT touched here — persisting an
        amendment is gated behind a success + Manager-reviewed promotion
        (task_wiki.resolve_plan_promotion). Logs the change to
        plan_amendments.log for audit. Operator (Claude) sees the log via cron
        supervision and reverts manually if needed.
        """
        section = str(amend.get("section") or "").strip()
        new_text = str(amend.get("new_text") or "").strip()
        reason = str(amend.get("reason") or "")
        if not section or not new_text:
            return
        allowed = {"Goal", "Recipe", "Hard rules", "Done gate",
                   "Success criteria"}
        if section not in allowed:
            return
        # Persistent seed is read-only; amend ONLY the ephemeral workspace plan.
        LHExecutor._amend_plan_section(sub_dir / "plan.md", section, new_text)
        log_path = sub_dir / "plan_amendments.log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] attempt={attempt_n} section='{section}' "
                     f"reason={reason}\n")
            for ln in new_text.splitlines():
                f.write(f"    {ln}\n")
            f.write("\n")

    @staticmethod
    def _amend_plan_section(plan_path: Path, section: str,
                            new_text: str) -> None:
        """Replace (or append) ``## <section>`` in plan_path with new_text."""
        import re
        if not plan_path.exists():
            return
        original = plan_path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"(^##\s+{re.escape(section)}[^\n]*\n)(.*?)(?=^##\s|\Z)",
            re.S | re.M,
        )
        new_body = new_text.rstrip() + "\n\n"
        if pattern.search(original):
            updated = pattern.sub(
                lambda m: m.group(1) + new_body, original, count=1)
        else:
            sep = "" if original.endswith("\n") else "\n"
            updated = original + sep + f"## {section}\n" + new_body
        plan_path.write_text(updated, encoding="utf-8")

    def _write_summary(self, workspace: Workspace,
                        r: LHExecutorResult,
                        spec: dict) -> None:
        lines = [f"# LH Summary · {r.lh_task}", ""]
        badge = "✓ SUCCESS" if r.success else "✗ FAIL"
        lines.append(f"**Outcome**: {badge}")
        lines.append(
            f"**Completed**: {r.completed_atomics}/{r.total_atomics} atomics"
        )
        if r.notes:
            lines.append(f"**Notes**: {r.notes}")
        lines.append("")
        lines.append("## Per-atomic attempts")
        for a in r.attempts:
            badge = "✓" if a.success else "✗"
            lines.append(
                f"- `{a.index:02d}_{a.atomic}` attempt {a.attempt}: {badge} "
                f"`{a.outcome}` ({a.tool_calls} tools) · "
                f"reviewer: `{a.review_verdict}`"
            )
            if not a.success and a.review_root_cause:
                lines.append(f"  · cause: {a.review_root_cause}")
        if r.mid_proposals:
            lines.append("")
            lines.append("## Mid-LH proposals (queued before end-of-LH)")
            for mp in r.mid_proposals:
                pid = mp.get("proposal_id", "?")
                src = mp.get("from", "")
                status = (mp.get("auto_apply_status")
                          or mp.get("html_review_path") or "queued")
                lines.append(f"- `{pid}` · from {src} · {status}")
        lines.append("")
        lines.append("## Overall plan")
        lines.append("See `lh_plan.md`.")
        (workspace.root / "lh_summary.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
