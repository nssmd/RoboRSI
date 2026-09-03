"""Pure-vision OWLv2-to-SAM object localization for LIBERO."""

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
        _requires_semantic_pointing,
        localize_precise,
    )

    if _requires_semantic_pointing(obj):
        return (
            {
                "ok": False,
                "reason": (
                    "fine-grained identity or relationship requires "
                    "find_by_pointing with the exact task wording"
                ),
            },
            obs,
        )

    try:
        uv = localize_precise(state, obj, route="owlv2")
    except (ImportError, OSError, RuntimeError) as exc:
        return (
            {
                "ok": False,
                "reason": (
                    "local detector is unavailable; use find_by_pointing or "
                    f"find_pixel instead ({type(exc).__name__})"
                ),
            },
            obs,
        )
    if uv is None:
        return (
            {
                "ok": False,
                "reason": f"detector could not find '{obj}'",
            },
            obs,
        )

    u, v = uv
    image = obs.images["head_camera"]
    shape = getattr(image, "shape", ())
    if len(shape) < 2 or not (0 <= int(u) < shape[1] and 0 <= int(v) < shape[0]):
        return ({"ok": False, "reason": "localizer returned an invalid pixel"}, obs)
    return (
        {
            "ok": True,
            "u": int(u),
            "v": int(v),
            "source": "owlv2->sam",
        },
        obs,
    )
