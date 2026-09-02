"""move_to_pixel — unproject a pixel then servo the EE above it (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero._helpers import resolve_camera


def dispatch_runtime(state, args: dict[str, Any]):
    if "u" not in args or "v" not in args:
        return ({"ok": False, "reason": "u and v (pixel) are required"},
                state.env.take_snapshot())
    camera = resolve_camera(args.get("camera"))
    world = state.env.pixel_to_world(int(args["u"]), int(args["v"]), camera)
    if world is None:
        return ({"ok": False, "reason": "depth unavailable"}, state.env.take_snapshot())
    approach_z = float(args.get("approach_z", 0.05))
    gripper = str(args.get("gripper") or "keep").strip().lower()
    ctrl = LiberoControl(state.env)
    target = [float(world[0]), float(world[1]), float(world[2]) + approach_z]
    reached, _ = ctrl.servo_to(target, gripper=gripper, max_iters=80)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": True, "reached": bool(reached),
             "world": [round(float(x), 4) for x in world],
             "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
