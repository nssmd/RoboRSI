"""base.robotwin.descend_tcp_to_z — closed-loop residual descend that
compensates cuRobo's z-undershoot, holds wrist orientation, and aborts on a
lateral IK wander.

cuRobo's plan_path / move_fingertip_to terminates ~1-3 cm ABOVE the commanded z
(documented in grasp_object). On thin / low objects the gripper jaws then close
on air ABOVE the object. This skill closes the loop: command a descend to
``target_z``, MEASURE the actual fingertip TCP z, and re-command progressively
LOWER until the measured TCP reaches ``target_z`` within ``tol_m`` or hits a
damage-cap floor.

TWO safety properties a translational descend must have, both learned from
match_blocks place failures:
  (1) ORIENTATION-HOLD: by default hold the wrist's CURRENT live orientation (a
      descend must not re-orient the held object); a passed quat that differs
      from current by more than ``max_tilt_deg`` is IGNORED (omitting the quat
      used to revert to top-down and flip a block held at a tilted place quat;
      a mis-ordered quat 121 deg off flipped blocks off the table).
  (2) XY-WANDER ABORT: at a marginal tilted-quat near-floor target cuRobo can
      return a wild elbow-flipped IK solution that leaves the TCP ~20-25 cm off
      the commanded xy while the z still reads at/below target. The skill
      validates ONLY z by default, so this passes as 'reached' and the caller
      releases into a flung pose. We now ABORT (reached:False) when the realized
      TCP drifts > ``max_xy_drift_m`` from the commanded xy, and require
      xy-proximity for any reached:True.

Pure motion primitive — reads only the robot's own EE pose, never task/target
ground-truth. No set_pose / teleport / attach.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _quat_wxyz_to_R(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = (w * w + x * x + y * y + z * z) ** 0.5
    if n < 1e-9:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])


def _ee_quat(impl, arm: str) -> list:
    """Current wrist (flange) orientation as wxyz, read from the live EE pose."""
    ee = (impl.robot.get_left_ee_pose() if arm == "left"
          else impl.robot.get_right_ee_pose())
    return [float(ee[3]), float(ee[4]), float(ee[5]), float(ee[6])]


def _quat_angle_deg(qa, qb) -> float:
    """Geodesic angle (deg) between two wxyz quaternions, sign-agnostic."""
    a = np.array([float(v) for v in qa], dtype=float)
    b = np.array([float(v) for v in qb], dtype=float)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    d = abs(float(np.dot(a / na, b / nb)))
    d = min(1.0, max(-1.0, d))
    return float(np.degrees(2.0 * np.arccos(d)))


def _tcp_now(impl, arm: str) -> np.ndarray:
    """Current fingertip TCP world position (reads the robot's own EE pose)."""
    from roborsi.embodied.sim.robotwin.gripper_geom import tcp_from_flange
    ee = (impl.robot.get_left_ee_pose() if arm == "left"
          else impl.robot.get_right_ee_pose())
    flange = np.array([float(ee[0]), float(ee[1]), float(ee[2])])
    R = _quat_wxyz_to_R(ee[3:7])
    return tcp_from_flange(impl, flange, R, arm)


def dispatch_runtime(state: Any, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_fingertip_to
    arm = str(args.get("arm", "")).lower()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"},
                _snapshot(state.env))
    if args.get("target_z") is None:
        return ({"ok": False,
                 "reason": "target_z (the z the fingertip TCP must actually reach) required"},
                _snapshot(state.env))
    impl = state.env._impl
    target_z = float(args["target_z"])
    tol = float(args.get("tol_m", 0.006))
    max_iters = int(args.get("max_iters", 8))
    min_step = float(args.get("min_step_m", 0.002))
    max_step = float(args.get("max_step_m", 0.012))
    floor_z = float(args.get("floor_z", target_z - 0.03))   # damage cap
    max_tilt_deg = float(args.get("max_tilt_deg", 25.0))
    max_xy_drift = float(args.get("max_xy_drift_m", 0.06))

    tcp0 = _tcp_now(impl, arm)
    x = float(args.get("x", tcp0[0]))     # hold current xy unless overridden
    y = float(args.get("y", tcp0[1]))

    # A descend is purely TRANSLATIONAL: hold the wrist's CURRENT orientation by
    # default, and refuse a passed quat that would flip it.
    cur_quat = _ee_quat(impl, arm)
    req_quat = args.get("quat")
    if req_quat is None:
        quat = cur_quat
        quat_note = "held current wrist orientation (no quat passed)"
    elif _quat_angle_deg(req_quat, cur_quat) <= max_tilt_deg:
        quat = list(req_quat)
        quat_note = "honoured requested quat (within max_tilt_deg of current)"
    else:
        quat = cur_quat
        quat_note = (f"IGNORED requested quat: it differs from the current wrist by "
                     f">{max_tilt_deg:.0f} deg — a descend is translational and must not "
                     f"flip the held object; held current orientation instead")

    obs = _snapshot(state.env)
    cmd_z = target_z                      # first command the literal target
    hist: list[float] = []
    for i in range(max_iters):
        cmd_z = max(floor_z, cmd_z)
        _res, obs = _do_move_fingertip_to(
            state, {"arm": arm, "x": x, "y": y, "z": cmd_z, "quat": quat})
        tcp = _tcp_now(impl, arm)
        z_now = float(tcp[2])
        xy_drift = float(((float(tcp[0]) - x) ** 2 + (float(tcp[1]) - y) ** 2) ** 0.5)
        hist.append(round(z_now, 4))
        # XY-WANDER ABORT: a wild cuRobo IK solution at a marginal tilted-quat
        # near-floor target leaves the TCP far from the commanded xy. The z may
        # even read 'reached'. Treat it as a FAILED descend so the caller does
        # NOT release onto a flung pose.
        if xy_drift > max_xy_drift:
            return ({"ok": False, "reached": False, "arm": arm,
                     "tcp_z": round(z_now, 4), "target_z": round(target_z, 4),
                     "tcp_xy": [round(float(tcp[0]), 4), round(float(tcp[1]), 4)],
                     "commanded_xy": [round(x, 4), round(y, 4)],
                     "xy_drift_m": round(xy_drift, 4),
                     "iters": i + 1, "z_history": hist, "quat_note": quat_note,
                     "reason": (f"ABORTED: TCP wandered {xy_drift * 100:.1f}cm from the "
                                f"commanded xy — cuRobo returned a wild IK solution at "
                                f"this tilted-quat near-floor target. DO NOT release "
                                f"(the held object would be flung); re-approach from a "
                                f"hover, or use a feasible target_z / a less-tilted quat.")},
                    obs)
        err = z_now - target_z            # >0 ⇒ TCP still ABOVE target (undershoot)
        if err <= tol:
            return ({"ok": True, "reached": True, "arm": arm,
                     "tcp_z": round(z_now, 4), "target_z": round(target_z, 4),
                     "xy_drift_m": round(xy_drift, 4),
                     "iters": i + 1, "z_history": hist, "quat_note": quat_note,
                     "reason": f"TCP reached target z (gap {err * 100:.1f}cm <= tol)"},
                    obs)
        if cmd_z <= floor_z + 1e-6:
            break                          # at damage cap, can't go lower
        cmd_z -= max(min_step, min(max_step, err))   # over-command downward by the residual

    tcp_fin = _tcp_now(impl, arm)
    z_fin = float(tcp_fin[2])
    xy_fin = float(((float(tcp_fin[0]) - x) ** 2 + (float(tcp_fin[1]) - y) ** 2) ** 0.5)
    reached = ((z_fin - target_z) <= tol) and (xy_fin <= max_xy_drift)
    return ({"ok": bool(reached), "reached": bool(reached), "arm": arm,
             "tcp_z": round(z_fin, 4), "target_z": round(target_z, 4),
             "xy_drift_m": round(xy_fin, 4),
             "iters": max_iters, "z_history": hist, "floor_z": round(floor_z, 4),
             "quat_note": quat_note,
             "reason": (f"TCP at z={z_fin:.4f} vs target {target_z:.4f} "
                        f"(gap {(z_fin - target_z) * 100:.1f}cm); xy_drift {xy_fin * 100:.1f}cm"
                        f"{' — hit floor' if z_fin <= floor_z + 1e-6 else ''}")},
            obs)


def run(env=None, **_: Any):
    raise RuntimeError(
        "descend_tcp_to_z runs inside the rollout tool loop; call via VLM tool dispatch.")
