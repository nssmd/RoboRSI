"""move_to_pose - IK-backed JOINT_POSITION motion to a world pose."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl

_RESIDUAL_CORRECTION_MAX_ERROR_M = 0.12
_RESIDUAL_CORRECTION_MAX_ITERS = 60


def dispatch_runtime(state, args: dict[str, Any]):
    pos = args.get("pos")
    if (
        not isinstance(pos, (list, tuple))
        or len(pos) != 3
        or any(isinstance(value, (bool, np.bool_)) for value in pos)
    ):
        return ({"ok": False, "reached": False, "reason": "pos must be [x, y, z]"},
                state.env.take_snapshot())
    try:
        pos = np.asarray(pos, dtype=float)
    except (TypeError, ValueError, OverflowError):
        pos = np.array([], dtype=float)
    if pos.shape != (3,) or not np.all(np.isfinite(pos)):
        return ({"ok": False, "reached": False, "reason": "pos must contain finite numbers"},
                state.env.take_snapshot())
    quat = args.get("quat")
    if quat is not None:
        if (
            not isinstance(quat, (list, tuple))
            or len(quat) != 4
            or any(isinstance(value, (bool, np.bool_)) for value in quat)
        ):
            return ({"ok": False, "reached": False, "reason": "quat must be [x, y, z, w]"},
                    state.env.take_snapshot())
        try:
            quat = np.asarray(quat, dtype=float)
        except (TypeError, ValueError, OverflowError):
            quat = np.array([], dtype=float)
        norm = float(np.linalg.norm(quat)) if quat.shape == (4,) else 0.0
        if (
            quat.shape != (4,)
            or not np.all(np.isfinite(quat))
            or norm <= np.finfo(float).eps
        ):
            return ({"ok": False, "reached": False, "reason": "quat must be finite and non-zero"},
                    state.env.take_snapshot())
        quat = quat / norm
    gripper = str(args.get("gripper") or "keep").strip().lower()
    max_iters = int(args.get("max_iters") or 80)
    via_trajopt = args.get("via_trajopt") is True
    planned_trajectory = args.get("_planned_trajectory")
    ctrl = LiberoControl(state.env)
    initial_ee, _, _ = ctrl.read_pose()
    initial_ee = np.asarray(initial_ee, dtype=float)
    if planned_trajectory is None:
        reached, _ = ctrl.servo_to(
            pos,
            quat=quat,
            gripper=gripper,
            max_iters=max_iters,
            via_trajopt=via_trajopt,
        )
    else:
        reached, _ = ctrl.execute_previewed_trajectory(
            planned_trajectory,
            pos=pos,
            quat=quat,
            gripper=gripper,
        )
    ee, _, _ = ctrl.read_pose()
    ee = np.asarray(ee, dtype=float)
    position_error = float(np.linalg.norm(pos - ee))
    correction_attempted = False
    correction_reached = False
    if (
        via_trajopt
        and not reached
        and 0.0 < position_error <= _RESIDUAL_CORRECTION_MAX_ERROR_M
    ):
        correction_attempted = True
        correction_reached, _ = ctrl.servo_correction_to(
            pos,
            quat=quat,
            gripper=gripper,
            max_iters=min(max(1, max_iters), _RESIDUAL_CORRECTION_MAX_ITERS),
        )
        reached = bool(correction_reached)
        ee, _, _ = ctrl.read_pose()
        ee = np.asarray(ee, dtype=float)
        position_error = float(np.linalg.norm(pos - ee))
    return ({"ok": bool(reached), "reached": bool(reached),
             "reason": None if reached else "target pose was not reached",
             "ee_pos": [round(float(v), 4) for v in ee],
             "position_error": round(position_error, 5),
             "moved_distance": round(float(np.linalg.norm(ee - initial_ee)), 5),
             "via_trajopt": via_trajopt,
             "preview_committed": planned_trajectory is not None,
             "residual_correction_attempted": correction_attempted,
             "residual_correction_reached": correction_reached},
            state.env.take_snapshot())
