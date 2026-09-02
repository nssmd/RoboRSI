"""find_pixel — object-center grounding on the latest camera frame (base/libero).

Pure vision: NO ground-truth pose read. Grounds a noun phrase to a pixel with
the shared Grounding-DINO + SAM detector (reused from the RoboTwin detector —
it is embodiment-agnostic: it only consumes an RGB array). Mirrors the RoboTwin
`find_pixel` so the LIBERO Engineer localizes the same way a camera-only robot
would: look() -> find_pixel(object) -> (u,v) -> unproject_pixel(u,v) -> world XYZ.
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
    # Reuse the shared Grounding-DINO + SAM detector (pure RGB in, no sim GT).
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    dets = detect(np.asarray(head), obj, top_k=3)
    if not dets:
        return ({"ok": False,
                 "reason": (f"Grounded-SAM did not find '{obj}'. Use a concrete "
                            "noun phrase ('red mug' not 'the thing') and look() "
                            "to refresh the image, then retry.")}, obs)
    top = dets[0]
    return ({"ok": True, "u": int(top.centroid[0]), "v": int(top.centroid[1]),
             "confidence": round(float(top.score), 3), "bbox": list(top.bbox),
             "n_alternatives": len(dets) - 1, "location": loc,
             "note": ("Grounded-SAM mask centroid (detector score, not a VLM "
                      "self-report). Feed u,v to unproject_pixel for world XYZ.")},
            obs)
