"""move_to_pixel — unproject a pixel then servo the EE above it (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero._helpers import (
    parse_image_pixel,
    resolve_camera,
)


def dispatch_runtime(state, args: dict[str, Any]):
    if "u" not in args or "v" not in args:
        return ({"ok": False, "reason": "u and v (pixel) are required"},
                state.env.take_snapshot())
    camera = resolve_camera(args.get("camera"))
    snapshot = state.env.take_snapshot()
    image_key = "wrist" if camera == "robot0_eye_in_hand" else "head_camera"
    images = getattr(snapshot, "images", {}) or {}
    uv = parse_image_pixel(
        [args["u"], args["v"]],
        images.get(image_key),
        fallback_hw=getattr(state.env, "_camera_hw", (256, 256)),
    )
    if uv is None:
        return (
            {"ok": False, "reason": "u and v must be finite in-frame integers"},
            snapshot,
        )
    world = state.env.pixel_to_world(uv[0], uv[1], camera)
    if world is None:
        return ({"ok": False, "reason": "depth unavailable"}, state.env.take_snapshot())
    approach_z = float(args.get("approach_z", 0.05))
    gripper = str(args.get("gripper") or "keep").strip().lower()
    ctrl = LiberoControl(state.env)
    target = [float(world[0]), float(world[1]), float(world[2]) + approach_z]
    reached, _ = ctrl.servo_to(target, gripper=gripper, max_iters=80)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": bool(reached), "reached": bool(reached),
             "reason": None if reached else "target pose was not reached",
             "world": [round(float(x), 4) for x in world],
             "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
