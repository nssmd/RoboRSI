"""base.robotwin.is_reachable — distance precheck + real cuRobo IK verify.

Two-stage:
  1. Cheap distance + midline filter (rejects obviously-out-of-reach early,
     no IK call needed).
  2. If heuristic passes, call gripper_geom.plan_and_predict_ee with a
     top-down approach pose; mark reachable=True only if cuRobo plans AND
     the FK'd plan terminus lands within tol_m of target.

Stage 2 catches the failure mode where the target is within arm radius but
joint limits / collision / singularity make cuRobo refuse — heuristic alone
mislabeled ~3/10 beat_block_hammer seeds as reachable that cuRobo refused
at execution time.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


_REACH_RADIUS = 0.75       # URDF link sum ≈ 0.7 m
_MIDLINE_SLACK = 0.05
_DEFAULT_TOPDOWN_QUAT_WXYZ = [0.5, -0.5, 0.5, 0.5]   # same as grasp_then_lift
_DEFAULT_IK_TOL_M = 0.02
_HOVER_M = 0.10            # plan to fingertip-hover above target, not on-surface


def _arm_base_world(impl, arm: str) -> tuple[float, float, float]:
    name = "fl_base_link" if arm == "left" else "fr_base_link"
    art = impl.scene.get_all_articulations()[0]
    link = next((l for l in art.get_links() if l.get_name() == name), None)
    if link is None:
        raise RuntimeError(f"is_reachable: link {name!r} not found in articulation")
    p = link.get_pose().p
    return float(p[0]), float(p[1]), float(p[2])


def _quat_wxyz_to_R(q: list[float]) -> np.ndarray:
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-9:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])


def _ik_check(impl, arm: str, tcp_xyz: list[float], quat_wxyz: list[float],
              tol_m: float) -> dict:
    """TCP→flange conversion (gripper_geom mesh constants), then cuRobo plan
    + FK-verify. Saves/restores qpos to a NEUTRAL home-pose start state so
    reachability is config-independent (otherwise cuRobo would mis-refuse
    targets that are physically reachable just because the arm's CURRENT
    qpos puts it on the wrong side of a kinematic branch)."""
    from roborsi.embodied.sim.robotwin.gripper_geom import (
        flange_from_tcp, plan_and_predict_ee,
    )
    R = _quat_wxyz_to_R(quat_wxyz)
    flange = flange_from_tcp(impl, np.asarray(tcp_xyz), R, arm)
    pose7 = [float(flange[0]), float(flange[1]), float(flange[2]), *quat_wxyz]

    art = impl.scene.get_all_articulations()[0]
    saved_qpos = np.array(art.get_qpos(), copy=True)
    try:
        neutral = _neutral_qpos(impl, saved_qpos)
        art.set_qpos(neutral)
        res = plan_and_predict_ee(impl, pose7, arm, tol_m=tol_m)
    finally:
        art.set_qpos(saved_qpos)

    return {
        "ik_ok": bool(res.get("ok")),
        "ik_status": res.get("status"),
        "ik_gap_m": res.get("gap_m"),
        "ik_reason": res.get("reason"),
        "flange_target": [round(float(v), 3) for v in flange.tolist()],
    }


_NEUTRAL_QPOS_CACHE: dict = {}


def _neutral_qpos(impl, current: np.ndarray) -> np.ndarray:
    """Mid-of-joint-limits qpos with grippers open. Cached after first call."""
    if "qpos" in _NEUTRAL_QPOS_CACHE:
        return _NEUTRAL_QPOS_CACHE["qpos"]
    art = impl.scene.get_all_articulations()[0]
    qmin, qmax = art.get_qlimit()[:, 0], art.get_qlimit()[:, 1]
    # Replace +/-inf with current value (continuous joints).
    qmin = np.where(np.isfinite(qmin), qmin, current)
    qmax = np.where(np.isfinite(qmax), qmax, current)
    neutral = 0.5 * (qmin + qmax)
    _NEUTRAL_QPOS_CACHE["qpos"] = neutral
    return neutral


def dispatch_runtime(state: Any, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    arm = str(args.get("arm", "")).lower()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"},
                _snapshot(state.env))
    x = args.get("x"); y = args.get("y"); z = args.get("z")
    if x is None or y is None or z is None:
        return ({"ok": False, "reason": "x, y, z required"}, _snapshot(state.env))
    x, y, z = float(x), float(y), float(z)
    quat = args.get("quat") or _DEFAULT_TOPDOWN_QUAT_WXYZ
    if isinstance(quat, str):
        import ast
        quat = ast.literal_eval(quat)
    quat = [float(q) for q in quat]
    tol_m = float(args.get("tol_m", _DEFAULT_IK_TOL_M))
    skip_ik = bool(args.get("skip_ik", False))
    hover_m = float(args.get("hover_m", _HOVER_M))

    impl = state.env._impl
    bx, by, bz = _arm_base_world(impl, arm)
    other_bx, _, _ = _arm_base_world(impl, "right" if arm == "left" else "left")
    midline_x = 0.5 * (bx + other_bx)
    dx, dy, dz = x - bx, y - by, z - bz
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    cross_midline = (arm == "left" and x > midline_x + _MIDLINE_SLACK) or \
                    (arm == "right" and x < midline_x - _MIDLINE_SLACK)
    heur_pass = (dist <= _REACH_RADIUS) and (not cross_midline)

    if not heur_pass:
        reason = (f"midline cross (arm={arm}, x={x:.3f} vs midline={midline_x:+.3f})"
                  if cross_midline
                  else f"dist {dist:.3f}m > reach {_REACH_RADIUS}m")
        return ({"ok": True, "arm": arm, "reachable": False, "reason": reason,
                 "stage": "heuristic", "distance_to_base": round(dist, 4),
                 "base_world_xyz": [round(bx, 3), round(by, 3), round(bz, 3)]},
                _snapshot(state.env))

    if skip_ik:
        return ({"ok": True, "arm": arm, "reachable": True,
                 "reason": f"heuristic-only (skip_ik=True); dist={dist:.3f}m",
                 "stage": "heuristic", "distance_to_base": round(dist, 4),
                 "base_world_xyz": [round(bx, 3), round(by, 3), round(bz, 3)]},
                _snapshot(state.env))

    # Stage 2: real cuRobo IK at hover pose (the pose we'd actually plan to first).
    ik = _ik_check(impl, arm, [x, y, z + hover_m], list(quat), tol_m)
    reachable = ik["ik_ok"]
    reason = (f"cuRobo IK ok at hover (gap={ik['ik_gap_m']}m, dist={dist:.3f}m)"
              if reachable else f"cuRobo IK refused: {ik['ik_reason']}")
    return ({
        "ok": True, "arm": arm, "reachable": reachable, "reason": reason,
        "stage": "ik",
        "distance_to_base": round(dist, 4),
        "base_world_xyz": [round(bx, 3), round(by, 3), round(bz, 3)],
        "ik_status": ik["ik_status"],
        "ik_gap_m": ik["ik_gap_m"],
        "ik_flange_target": ik["flange_target"],
        "note": ("Stage1 distance/midline + Stage2 cuRobo IK at hover-pose. "
                 "reachable=True ⇒ a top-down approach is physically planable; "
                 "still no guarantee of grasp success."),
    }, _snapshot(state.env))


def run(env, arm: str, x: float, y: float, z: float, quat=None, **_: Any):
    raise RuntimeError(
        "is_reachable runs inside the rollout tool loop; call via VLM tool dispatch."
    )
