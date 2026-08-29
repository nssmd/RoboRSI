"""descend_tcp_to_z — vertical servo of the grip site to a target z (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def dispatch_runtime(state, args: dict[str, Any]):
    if "target_z" not in args:
        return ({"ok": False, "reason": "target_z is required"},
                state.env.take_snapshot())
    ctrl = LiberoControl(state.env)
    cur, _, _ = ctrl.read_pose()
    x = float(args.get("x", cur[0]))
    y = float(args.get("y", cur[1]))
    z = float(args["target_z"])
    gripper = str(args.get("gripper") or "keep").strip().lower()
    max_iters = int(args.get("max_iters") or 60)
    reached, _ = ctrl.servo_to([x, y, z], gripper=gripper, max_iters=max_iters)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": bool(reached), "reached": bool(reached),
             "reason": None if reached else "target pose was not reached",
             "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
