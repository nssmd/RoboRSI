"""Engineer role — Opus drives the sim tool loop directly.

Per user spec (2026-06-09 design call): the Engineer must execute the
sim itself, NOT delegate to a separate Sonnet inner loop. So this is
a thin wrapper around run_rollout pinned to Opus model. The
rollout infrastructure does the per-step tool dispatch; the LLM
driving every step is Opus.

Flow:
  1. Read plan.md
  2. If active base/robotwin skill count > SKILL_LIST_SOFT_CAP,
     call SkillSelector (Sonnet sub-agent) to pick top-15 names.
  3. Build engineer instruction string from mission_spec + plan.md.
  4. Call run_rollout(model=opus, restrict_to_names=top_k).
  5. Write summary.md with outcome + key trace highlights.
  6. Return result dict for Reviewer to consume.
"""
from __future__ import annotations

from typing import Any

from roborsi.agents.workspace import Workspace
from roborsi.agents.skill_selector import (
    SkillSelector, SKILL_LIST_SOFT_CAP,
)
from roborsi.agents.skill_history import record_success
from roborsi.agents.plan_archive import archive_successful_plan


_ENGINEER_MODEL = "anthropic/claude-opus-4-8"


def _count_active_skills() -> int:
    """How many base/robotwin skills are currently registered + wired."""
    from roborsi.embodied.skills import discover
    from roborsi.embodied.agent_loop.prompt_tools import _try_load_plugin_dispatcher
    wired = 0
    for sk in discover():
        parts = sk.path.parent.parts
        if "base" in parts and "robotwin" in parts:
            if _try_load_plugin_dispatcher(sk.name) is not None:
                wired += 1
    return wired


def _build_skill_index() -> str:
    """Render the full base/robotwin skill index (no restriction).
    Used as SkillSelector input."""
    from roborsi.embodied.agent_loop.prompt_tools import _build_tools_block
    return _build_tools_block(restrict_to_names=None)


def _summarize_trace(trace: list[dict[str, Any]], limit: int = 12) -> str:
    """One-line-per-step rendering of the inner VLM trace."""
    if not trace:
        return "  (empty trace)"
    out = []
    for step in trace[:limit]:
        tool_call = step.get("tool_call") or {}
        name = tool_call.get("tool", "?")
        args = str(tool_call.get("args") or {})[:80]
        res = step.get("result") or {}
        ok = res.get("ok") if isinstance(res, dict) else None
        out.append(f"  [{step.get('step','?')}] {name}({args}) → ok={ok}")
    if len(trace) > limit:
        out.append(f"  ... ({len(trace) - limit} more)")
    return "\n".join(out)


class Engineer:
    """Drives the sim tool loop as Opus. Reads plan.md, writes summary.md."""

    DEFAULT_MODEL = _ENGINEER_MODEL

    def __init__(self, model: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL

    def execute(self, *, mission_spec: dict[str, Any],
                 workspace: Workspace, seed: int,
                 tool_budget: int = 24,
                 backend_name: str = "robotwin",
                 sim_task: str | None = None) -> dict[str, Any]:
        """Run one atomic episode end-to-end. Owns env lifecycle
        (backend.make_env context manager). Returns result dict
        including success, outcome, trace, and writes summary.md.

        ``sim_task`` is the task string handed to ``make_env`` when it differs
        from the skill name — LIBERO atomics map ``libero_pick_place`` →
        ``libero_object/0``. Defaults to ``workspace.task`` (RoboTwin, where the
        atomic name IS the sim env name)."""
        from roborsi.embodied.agent_loop import get_backend
        from roborsi.embodied.agent_loop.rollout import run_rollout

        plan_md = workspace.read_plan()
        goal = mission_spec.get("goal", workspace.task)
        success_criteria = "; ".join(mission_spec.get("success_criteria", [])) \
                            or "done(success=True)"

        # ── Skill selection: top-K when registry exceeds cap ──
        restrict: set[str] | None = None
        n_active = _count_active_skills()
        if n_active > SKILL_LIST_SOFT_CAP:
            from roborsi.agents.skill_history import get_success_counts
            selector = SkillSelector()
            picked = selector.pick(
                plan_md=plan_md,
                recent_results=[],
                skill_index=_build_skill_index(),
                scene_hint=f"task={workspace.task} seed={seed}",
                success_counts=get_success_counts(workspace.task),
            )
            if picked:
                restrict = set(picked)

        # ── Compose engineer instruction ──
        instruction = (
            f"GOAL: {goal}\n\n"
            f"PLAN (from Planner — follow this; amend only if scene differs):\n"
            f"{plan_md}\n\n"
            f"SUCCESS CRITERIA: {success_criteria}\n\n"
            f"DISCIPLINE: any throwaway debug/probe/calibration script you write goes "
            f"in THIS skill's own folder (embodied/skills/<tier>/<name>/scripts/), never "
            f"the repo-root scripts/. When done, bake the result into policy.py/plan.md "
            f"(cite it in a comment) and DELETE the script."
        )

        # ── Drive sim loop. Backend context-manages env lifecycle. ──
        backend = get_backend(backend_name)
        ok, reason = backend.available()
        if not ok:
            raise RuntimeError(f"backend '{backend_name}' unavailable: {reason}")
        with backend.make_env(sim_task or workspace.task,
                              {"require_depth": True}) as env:
            env.reset(seed)
            m_result = run_rollout(
                env, seed=seed, task_name=workspace.task,
                instruction=instruction,
                expected_on_success=success_criteria,
                model=self.model, tool_budget=tool_budget,
                workdir=workspace.root / "rollout",
                restrict_to_names=restrict,
                # Standalone atomic ⇔ one sim task: gate success on the sim's
                # OWN check_success, not the VLM's done() self-report. Without
                # this, the VLM overclaiming done(success=True) (e.g. lift_pot
                # arms close on air, pot never leaves the table) is recorded as
                # a "success" + demo. LH sub-atomics keep this False (their
                # check_success is the FULL-task predicate, judged by progress_judge).
                use_sim_predicate=True,
            )

        rollout = m_result.rollout
        trace = m_result.trace
        result = {
            "success": bool(rollout.success),
            "outcome": rollout.outcome,
            "tool_calls": len(trace),
            "trace": trace,
            "rollout_meta": dict(rollout.meta),
            "restricted_skills": sorted(restrict) if restrict else None,
            "n_active_skills": n_active,
        }

        # ── Record per-(task, skill) success for SkillSelector ranking ──
        if result["success"]:
            skills_used = sorted({
                (step.get("tool_call") or {}).get("tool")
                for step in trace
                if (step.get("tool_call") or {}).get("tool")
            } - {"done", "look", "find_pixel", "unproject_pixel"})
            record_success(task=workspace.task, skills_used=list(skills_used))
            # Archive the plan that produced this success so the next
            # Planner call for the same task can reference it.
            archive_successful_plan(
                task=workspace.task,
                plan_md=plan_md,
                skills_used=list(skills_used),
            )

        # ── Write summary.md (Engineer's own writeup, not the trace dump) ──
        summary_lines = [
            f"# Engineer Summary · {workspace.task} (seed={seed})",
            "",
            f"**Outcome**: `{result['outcome']}` "
            f"({'SUCCESS' if result['success'] else 'FAIL'})",
            f"**Tool calls**: {result['tool_calls']}",
            f"**Skills exposed**: {n_active} active"
            + (f" · narrowed to {len(restrict)} via SkillSelector"
               if restrict else " (full list, below SKILL_LIST_SOFT_CAP)"),
            "",
            "## Goal",
            goal,
            "",
            "## Trace (first 12 steps)",
            "```",
            _summarize_trace(trace),
            "```",
        ]
        if restrict:
            summary_lines += [
                "",
                "## Skills selected for this episode",
                ", ".join(sorted(restrict)),
            ]
        workspace.write_summary("\n".join(summary_lines) + "\n")
        return result
