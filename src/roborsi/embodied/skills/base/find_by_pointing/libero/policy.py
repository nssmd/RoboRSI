"""Pure-vision VLM-pointing-to-SAM object localization for LIBERO."""

from __future__ import annotations

from typing import Any


def dispatch_runtime(state: Any, args: dict[str, Any]):
    obs = state.env.take_snapshot()
    if obs.images.get("head_camera") is None:
        return (
            {"ok": False, "reason": "no head_camera image; call look() first"},
            obs,
        )

    obj = str(args.get("object") or "").strip()
    if not obj:
        return ({"ok": False, "reason": "object is required"}, obs)

    from roborsi.embodied.skills.base._lib.libero._perception import (
        localize_precise,
    )

    uv = localize_precise(state, obj, route="vlm_sam")
    if uv is None:
        return (
            {
                "ok": False,
                "reason": f"pointer could not find '{obj}'",
            },
            obs,
        )

    u, v = uv
    image = obs.images["head_camera"]
    shape = getattr(image, "shape", ())
    if len(shape) < 2 or not (0 <= int(u) < shape[1] and 0 <= int(v) < shape[0]):
        return ({"ok": False, "reason": "localizer returned an invalid pixel"}, obs)
    from roborsi.embodied.skills.base._lib.libero.semantic_point import (
        record_semantic_point,
    )

    record_semantic_point(
        state.env,
        object_name=obj,
        pixel=(int(u), int(v)),
        frame=image,
        source="vlm->sam",
    )
    return (
        {
            "ok": True,
            "u": int(u),
            "v": int(v),
            "source": "vlm->sam",
        },
        obs,
    )
