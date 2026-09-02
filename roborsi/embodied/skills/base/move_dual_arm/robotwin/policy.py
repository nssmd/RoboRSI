"""base.robotwin.move_dual_arm — move BOTH arms to target flange poses in
ONE synchronized impl.move() call so cuRobo plans the two arms together.

Single-arm move_to_pose moves one arm at a time, so when the two arms
converge for a bowl-to-bowl tilt-pour the first arm (and the bowl it
holds) blocks the second — V78 review: "arms jam at elbow on
convergence; move_to_pose returns partial plans, bowls never docked."
The BiCoord expert avoids this by issuing both arms in one move:
    self.move(self.move_to_pose(left, pose_L),
              self.move_to_pose(right, pose_R))
This tool exposes exactly that primitive. The Engineer/Reviewer choose
the two target poses themselves (no hardcoded values); for a pour the
left bowl's quat should already be a tilted (mouth-down) orientation so
the dock + pour happen in this single synchronized motion.

SAFETY GATE (added after handover_bowls_via_tilt failed 4× with the same
mode — a partial/infeasible joint plan still EXECUTED and dragged the
held bowls off the table): before calling impl.move we PRE-VALIDATE both
target flange poses with cuRobo (gripper_geom.plan_and_predict_ee on a
neutral start qpos, scene restored — no mutation). If either pose is
infeasible we ABORT WITHOUT MOVING, so a payload that is currently held
aloft is never jostled loose by a doomed partial motion. Opt out with
require_plan=False for the legacy unconditional behavior.
"""
from __future__ import annotations

import ast
from typing import Any

import numpy as np


def _parse_pose(v: Any) -> list[float]:
    if isinstance(v, str):
        v = ast.literal_eval(v)
    return [float(x) for x in v]


def _xyz_dist(a: Any, b: Any) -> float:
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5


_NEUTRAL_QPOS_CACHE: dict = {}


def _neutral_qpos(impl, current: np.ndarray) -> np.ndarray:
    """Mid-of-joint-limits qpos (continuous joints -> current). Cached."""
    if "qpos" in _NEUTRAL_QPOS_CACHE:
        return _NEUTRAL_QPOS_CACHE["qpos"]
    art = impl.scene.get_all_articulations()[0]
    qmin, qmax = art.get_qlimit()[:, 0], art.get_qlimit()[:, 1]
    qmin = np.where(np.isfinite(qmin), qmin, current)
    qmax = np.where(np.isfinite(qmax), qmax, current)
    neutral = 0.5 * (qmin + qmax)
    _NEUTRAL_QPOS_CACHE["qpos"] = neutral
    return neutral


def _preflight_plan(impl, arm: str, flange_pose7: list[float],
                    tol_m: float) -> dict:
    """cuRobo plan-check of a FLANGE pose (the same pose impl.move targets)
    from a neutral start qpos; scene qpos saved/restored (no mutation).
    Returns {ok, status, gap_m, reason}. On ANY exception, returns ok=True
    so the gate fails OPEN (never blocks a move because the checker itself
    errored — the legacy behavior is the safe fallback there)."""
    try:
        from roborsi.embodied.sim.robotwin.gripper_geom import (
            plan_and_predict_ee,
        )
    except Exception as e:  # checker unavailable -> fail open
        return {"ok": True, "status": "no_checker", "gap_m": None,
                "reason": f"plan_and_predict_ee import failed: {e}"}
    art = impl.scene.get_all_articulations()[0]
    saved_qpos = np.array(art.get_qpos(), copy=True)
    try:
        neutral = _neutral_qpos(impl, saved_qpos)
        art.set_qpos(neutral)
        res = plan_and_predict_ee(impl, list(flange_pose7), arm, tol_m=tol_m)
        return {"ok": bool(res.get("ok")), "status": res.get("status"),
                "gap_m": res.get("gap_m"), "reason": res.get("reason")}
    except Exception as e:  # planner threw -> fail open
        return {"ok": True, "status": "checker_error", "gap_m": None,
                "reason": f"plan_and_predict_ee raised: {e}"}
    finally:
        try:
            art.set_qpos(saved_qpos)
        except Exception:
            pass


def dispatch_runtime(state, args: dict[str, Any]):
    from envs.utils.action import Action, ArmTag
    from roborsi.embodied.agent_loop.rollout import _snapshot

    left_pose = args.get("left_pose")
    right_pose = args.get("right_pose")
    if left_pose is None or right_pose is None:
        return ({"ok": False, "reason": "left_pose and right_pose required, "
                 "each [x,y,z,qw,qx,qy,qz] (7 numbers)"}, _snapshot(state.env))
    left_pose = _parse_pose(left_pose)
    right_pose = _parse_pose(right_pose)
    if len(left_pose) != 7 or len(right_pose) != 7:
        return ({"ok": False, "reason": "each pose must be 7 numbers "
                 "[x,y,z,qw,qx,qy,qz]"}, _snapshot(state.env))

    require_plan = bool(args.get("require_plan", True))
    tol_m = float(args.get("tol_m", 0.03))

    impl = state.env._impl

    # --- SAFETY GATE: pre-validate BOTH poses before any physical motion ---
    # The targets passed to impl.move are FLANGE poses, so we plan-check the
    # poses directly (no TCP conversion). If either is infeasible we abort
    # WITHOUT moving, leaving any held payload undisturbed.
    if require_plan:
        l_pf = _preflight_plan(impl, "left", left_pose, tol_m)
        r_pf = _preflight_plan(impl, "right", right_pose, tol_m)
        if not (l_pf["ok"] and r_pf["ok"]):
            return ({
                "ok": False,
                "aborted_before_move": True,
                "stage": "preflight",
                "left_plan_ok": l_pf["ok"], "right_plan_ok": r_pf["ok"],
                "left_plan": l_pf, "right_plan": r_pf,
                "reason": ("ABORTED WITHOUT MOVING — cuRobo could not plan "
                           f"{'left' if not l_pf['ok'] else 'right'} target "
                           "(and possibly both). Executing a partial/infeasible "
                           "joint plan would drag any held payload off the "
                           "table (handover_bowls_via_tilt drop mode). Pick "
                           "dock poses that BOTH pass is_reachable / "
                           "check_dual_arm_collision, or raise z / move the "
                           "two targets farther apart, then retry."),
                "note": ("Both targets must be cuRobo-planable from a neutral "
                         "start before a synchronized dual-arm move executes. "
                         "Set require_plan=False to bypass (legacy: may drop "
                         "held objects on partial plans)."),
            }, _snapshot(state.env))

    impl.plan_success = True
    lb = impl.robot.get_left_ee_pose()
    rb = impl.robot.get_right_ee_pose()
    # ONE synchronized dual-arm move — cuRobo plans both arms together.
    impl.move(
        (ArmTag("left"), [Action(ArmTag("left"), "move", target_pose=left_pose)]),
        (ArmTag("right"), [Action(ArmTag("right"), "move", target_pose=right_pose)]),
    )
    la = impl.robot.get_left_ee_pose()
    ra = impl.robot.get_right_ee_pose()
    plan_ok = bool(getattr(impl, "plan_success", True))
    l_reached = _xyz_dist(la, left_pose) < 0.03
    r_reached = _xyz_dist(ra, right_pose) < 0.03
    l_moved = _xyz_dist(la, lb) > 0.005
    r_moved = _xyz_dist(ra, rb) > 0.005
    ok = plan_ok and l_reached and r_reached
    return ({
        "ok": ok,
        "plan_success": plan_ok,
        "left_reached": l_reached, "right_reached": r_reached,
        "left_moved": l_moved, "right_moved": r_moved,
        "left_ee_after": [round(float(v), 4) for v in la[:3]],
        "right_ee_after": [round(float(v), 4) for v in ra[:3]],
        "left_target": [round(v, 4) for v in left_pose[:3]],
        "right_target": [round(v, 4) for v in right_pose[:3]],
        "note": ("both arms reached their targets in one synchronized move"
                 if ok else
                 "NOT both reached — partial plan / cross-arm collision / "
                 "unreachable IK for the joint motion. Try poses higher up "
                 "(more z), farther apart, or pre-check with "
                 "check_dual_arm_collision / is_reachable on each pose. "
                 "(Preflight passed but the JOINT plan still came up short — "
                 "the two arms likely collide at the dock; separate them.)"),
    }, _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
