"""base.robotwin.plan — formalized 4-phase planning skill.

VLM emits this BEFORE any action and any time the plan needs revision.
Stores the plan in module state so other parts of the runtime (status
check) can surface success_evidence to VLM.
"""
from __future__ import annotations

import time
from typing import Any


_LAST_PLAN: dict[str, Any] = {}


def get_active_plan() -> dict[str, Any]:
    """Read-only accessor used by robotwin_agent STATUS CHECK to surface
    the current substep's success_evidence."""
    return dict(_LAST_PLAN)


def _reset_active_plan() -> None:
    """Clear the module-global plan at the start of each rollout. Without this
    a single-process runner (all seeds/tasks in one interpreter) leaks the
    prior episode's plan, so the next episode's first plan() call reports
    is_revision=True and inherits stale substeps. run_rollout calls this before
    its step loop so is_revision is per-episode correct."""
    _LAST_PLAN.clear()


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    goal = (args.get("goal") or "").strip()
    if not goal:
        return ({"ok": False, "reason": "goal (str) required"},
                _snapshot(state.env))
    scene_summary = (args.get("scene_summary") or "").strip()
    substeps = args.get("substeps")
    reason = (args.get("reason_for_revision") or "").strip()
    if not isinstance(substeps, list) or not substeps:
        return ({"ok": False, "reason": "substeps (list[dict]) required"},
                _snapshot(state.env))

    norm: list[dict[str, Any]] = []
    warnings: list[str] = []
    for i, s in enumerate(substeps):
        if not isinstance(s, dict):
            return ({"ok": False,
                     "reason": f"substep[{i}] must be dict"},
                    _snapshot(state.env))
        if not s.get("name") or not s.get("primary"):
            return ({"ok": False,
                     "reason": f"substep[{i}] missing required name/primary"},
                    _snapshot(state.env))
        prog = s.get("progress_pct")
        if prog is None:
            warnings.append(f"substep[{i}] '{s['name']}' missing progress_pct")
            prog = -1
        else:
            try:
                prog = int(prog)
            except (TypeError, ValueError):
                warnings.append(f"substep[{i}] progress_pct not int: {prog!r}")
                prog = -1
        evidence = (s.get("success_evidence") or "").strip()
        if not evidence:
            warnings.append(
                f"substep[{i}] '{s['name']}' missing success_evidence — "
                "STATUS CHECK won't have a target to verify against")
        fallback = (s.get("fallback") or "").strip()
        if not fallback:
            warnings.append(
                f"substep[{i}] '{s['name']}' missing fallback — "
                "you'll have nothing to switch to on RETRY")
        precond = s.get("preconditions") or []
        if not isinstance(precond, list):
            warnings.append(f"substep[{i}] preconditions not list")
            precond = []
        norm.append({
            "idx": i, "name": str(s["name"]),
            "primary": str(s["primary"]),
            "progress_pct": prog,
            "preconditions": [str(p) for p in precond],
            "success_evidence": evidence,
            "fallback": fallback,
        })

    # Monotonic progress check (lenient — warn, don't reject).
    progs = [s["progress_pct"] for s in norm if s["progress_pct"] >= 0]
    if progs:
        if any(b <= a for a, b in zip(progs, progs[1:])):
            warnings.append(
                "progress_pct not strictly monotonic increasing across "
                f"substeps: {progs}")
        if progs[-1] != 100:
            warnings.append(
                f"final substep progress_pct={progs[-1]}, expected 100")
    if not 3 <= len(norm) <= 7:
        warnings.append(
            f"substep count={len(norm)} outside recommended 3-7 "
            "(<3 too coarse, >7 too fine)")
    if not scene_summary:
        warnings.append(
            "scene_summary empty — recommended to call look() first and "
            "describe what you see before planning")

    is_revision = bool(_LAST_PLAN)
    plan_id = f"plan-{int(time.time() * 1000)}"
    _LAST_PLAN.update({
        "id": plan_id, "goal": goal, "scene_summary": scene_summary,
        "substeps": norm, "cursor": 0,
        "completed_idxs": [], "retry_counts": [0] * len(norm),
        "is_revision": is_revision,
        "reason_for_revision": reason,
        "registered_at": time.time(),
    })
    return ({
        "ok": True, "plan_id": plan_id,
        "n_substeps": len(norm),
        "total_progress_pct": progs[-1] if progs else 0,
        "validation_warnings": warnings,
        "is_revision": is_revision,
        "reason_for_revision": reason if is_revision else None,
        "note": (
            "Plan recorded. STATUS CHECK after each action turn will "
            "surface the current substep's success_evidence so you can "
            "verify objectively. Re-emit plan() any time to revise — "
            f"{'this is a revision' if is_revision else 'this is the initial plan'}."
            + (f" {len(warnings)} validation warning(s) — fix them or accept."
               if warnings else "")),
    }, _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
