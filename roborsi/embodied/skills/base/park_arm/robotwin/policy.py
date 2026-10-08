"""base.robotwin.park_arm — move one arm to a safe back-corner pose
while preserving its current gripper state.

Reuses the same _do_move_to_pose primitive every other motion skill
uses, so park inherits cuRobo IK + planner. The default park pose
(±0.38, -0.40, 1.05) puts the arm at the back corner of its half of
the workspace — far from the workspace midline where cross-arm
grasps enter.

Held objects travel with the arm via the gripper's continued closed-grip
friction — there is no attach constraint (that would fake the hold).
"""
from __future__ import annotations

from typing import Any


_DEFAULT_PARK = {
    "left": (-0.38, -0.40, 1.05),
    "right": (+0.38, -0.40, 1.05),
}
_TOP_DOWN_QUAT = [0.5, -0.5, 0.5, 0.5]


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_to_pose, _pose_components

    arm = args.get("arm")
    if not isinstance(arm, str) or arm not in ("left", "right"):
        return ({"ok": False, "success": False,
                 "reason": "arm must be 'left' or 'right'"}, _snapshot(state.env))

    dx, dy, dz = _DEFAULT_PARK[arm]
    raw_pose = {
        key: default if args.get(key) is None else args[key]
        for key, default in (("x", dx), ("y", dy), ("z", dz))
    }
    raw_pose["quat"] = args.get("quat")
    try:
        (x, y, z), quat = _pose_components(raw_pose)
    except ValueError as exc:
        return ({"ok": False, "success": False,
                 "reason": str(exc)}, _snapshot(state.env))

    # Hand off to the standard move primitive. keep_grip=True means we
    # simply don't issue any gripper command — the fingers stay closed, so
    # grip friction carries the held object along.
    move_res, obs = _do_move_to_pose(state, {
        "arm": arm, "x": x, "y": y, "z": z, "quat": quat,
    })
    keep_grip = bool(args.get("keep_grip", True))

    res = {
        "ok": bool(move_res.get("ok")),
        "arm": arm,
        "park_pose": [x, y, z, *quat],
        "ee_after": move_res.get("ee_after"),
        "delta_m": move_res.get("delta_m"),
        "kept_grip": keep_grip,
    }
    if not move_res.get("ok"):
        res["reason"] = (
            f"park IK refused at ({x:.3f},{y:.3f},{z:.3f}): "
            f"{move_res.get('note','')[:120]}"
        )
    return (res, obs)
