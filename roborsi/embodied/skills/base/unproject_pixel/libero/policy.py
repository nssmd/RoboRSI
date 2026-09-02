"""unproject_pixel — pixel (u,v) → world XYZ via depth + camera matrices (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._helpers import resolve_camera


def dispatch_runtime(state, args: dict[str, Any]):
    if "u" not in args or "v" not in args:
        return ({"ok": False, "reason": "u and v (pixel) are required"},
                state.env.take_snapshot())
    camera = resolve_camera(args.get("camera"))
    world = state.env.pixel_to_world(int(args["u"]), int(args["v"]), camera)
    if world is None:
        return ({"ok": False, "reason": "depth unavailable (env built without camera_depths)"},
                state.env.take_snapshot())
    return ({"ok": True, "world": [round(float(x), 4) for x in world]},
            state.env.take_snapshot())
