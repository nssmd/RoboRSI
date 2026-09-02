"""move_to_pose — OSC servo the end-effector to a world pose (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def dispatch_runtime(state, args: dict[str, Any]):
    pos = args.get("pos")
    if not isinstance(pos, (list, tuple)) or len(pos) != 3:
        return ({"ok": False, "reason": "pos must be [x, y, z]"},
                state.env.take_snapshot())
    quat = args.get("quat")
    if isinstance(quat, (list, tuple)) and len(quat) != 4:
        return ({"ok": False, "reason": "quat must be [x, y, z, w]"},
                state.env.take_snapshot())
    gripper = str(args.get("gripper") or "keep").strip().lower()
    max_iters = int(args.get("max_iters") or 80)
    ctrl = LiberoControl(state.env)
    reached, _ = ctrl.servo_to(
        pos, quat=quat if quat else None, gripper=gripper, max_iters=max_iters)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": True, "reached": bool(reached),
             "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
