"""unproject_pixel — pixel (u,v) → world XYZ via depth + camera matrices (base/libero)."""

from __future__ import annotations

from typing import Any

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
        return ({"ok": False, "reason": "depth unavailable (env built without camera_depths)"},
                snapshot)
    return ({"ok": True, "world": [round(float(x), 4) for x in world]},
            snapshot)
