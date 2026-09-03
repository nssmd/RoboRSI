"""find_pixel — object-center grounding on the latest camera frame (base/libero).

Pure vision: NO ground-truth pose read. A perception model points at the named
object in RGB; Grounding-DINO is an optional local fallback. The resulting
pixel can be unprojected through visible depth or passed directly to a
code-backed grasp skill.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.take_snapshot()
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera image — call look() first"},
                obs)
    obj = args.get("object", "the target")
    loc = str(args.get("location", ""))
    from roborsi.embodied.skills.base._lib.libero._perception import (
        remember_pixel,
        vlm_point,
    )

    try:
        point = vlm_point(state, str(obj), loc)
    except Exception:
        point = None
    if point is not None:
        point = remember_pixel(state, str(obj), point)
        return ({
            "ok": True,
            "u": int(point[0]),
            "v": int(point[1]),
            "confidence": None,
            "bbox": None,
            "n_alternatives": 0,
            "location": loc,
            "note": "Perception-model point grounded from the visible head image.",
        }, obs)

    # Deterministic local fallback when the detector assets are installed.
    try:
        from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect

        dets = detect(np.asarray(head), obj, top_k=3)
    except Exception:
        dets = []
    if not dets:
        return ({"ok": False,
                 "reason": (f"Vision grounding did not find '{obj}'. Use a concrete "
                            "noun phrase ('red mug' not 'the thing') and look() "
                            "to refresh the image, then retry.")}, obs)
    top = dets[0]
    point = remember_pixel(state, str(obj), top.centroid)
    return ({"ok": True, "u": int(top.centroid[0]), "v": int(top.centroid[1]),
             "confidence": round(float(top.score), 3), "bbox": list(top.bbox),
             "n_alternatives": len(dets) - 1, "location": loc,
             "note": "Grounded-SAM mask centroid. Feed u,v to unproject_pixel."},
            obs)
