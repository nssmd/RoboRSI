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
    # Use the local detector when its assets are installed. Offline deployments
    # fall back to the perception pointer.
    try:
        from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect

        dets = detect(np.asarray(head), obj, top_k=3)
    except Exception:
        dets = []
    if not dets:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            localize_precise,
            remember_pixel,
        )

        point = localize_precise(state, str(obj), route="vlm_sam")
        if point is None:
            return ({"ok": False,
                     "reason": (f"Vision grounding did not find '{obj}'. Use a "
                                "concrete noun phrase and refresh the image.")}, obs)
        point = remember_pixel(state, str(obj), point)
        return ({
            "ok": True,
            "u": point[0],
            "v": point[1],
            "confidence": None,
            "bbox": None,
            "n_alternatives": 0,
            "alternatives": [],
            "location": loc,
            "note": "Perception pointer grounded from the visible head image.",
        }, obs)
    height, width = np.asarray(head).shape[:2]

    def box_fraction(det) -> float:
        x1, y1, x2, y2 = [float(value) for value in det.bbox]
        return max(0.0, x2 - x1) * max(0.0, y2 - y1) / max(
            1.0,
            float(width * height),
        )

    dets = sorted(
        dets,
        key=lambda det: (
            box_fraction(det) > 0.45,
            -float(det.score),
        ),
    )
    candidates = [
        {
            "u": int(det.centroid[0]),
            "v": int(det.centroid[1]),
            "confidence": round(float(det.score), 3),
            "bbox": list(det.bbox),
            "source": "detector",
        }
        for det in dets
    ]
    from roborsi.embodied.skills.base._lib.libero import _perception

    semantic_query = _perception._semantic_point_query(str(obj))
    fine_grained = _perception._requires_semantic_pointing(str(obj))
    selected = None
    if fine_grained:
        selected = _perception._choose_localization_candidate(
            state,
            semantic_query,
            np.asarray(head),
            candidates,
        )
        if selected is None:
            return (
                {
                    "ok": False,
                    "reason": (
                        "fine-grained identity was not visually verified; "
                        "use find_by_pointing with the exact task wording"
                    ),
                    "alternatives": candidates,
                    "n_alternatives": len(candidates),
                    "location": loc,
                },
                obs,
            )
    if selected is None:
        selected = (
            int(candidates[0]["u"]),
            int(candidates[0]["v"]),
        )
    selected_index = next(
        (
            index
            for index, candidate in enumerate(candidates)
            if (
                int(candidate["u"]),
                int(candidate["v"]),
            )
            == selected
        ),
        0,
    )
    top = dets[selected_index]
    from roborsi.embodied.skills.base._lib.libero._perception import (
        remember_pixel,
    )

    remember_pixel(state, str(obj), top.centroid)
    alternatives = [
        {
            key: value
            for key, value in candidate.items()
            if key != "source"
        }
        for index, candidate in enumerate(candidates)
        if index != selected_index
    ]
    return ({"ok": True, "u": int(top.centroid[0]), "v": int(top.centroid[1]),
             "confidence": round(float(top.score), 3), "bbox": list(top.bbox),
             "n_alternatives": len(alternatives),
             "alternatives": alternatives,
             "location": loc,
             "note": ("Grounded-SAM mask centroid (detector score, not a VLM "
                      "self-report). Rank 1 is not guaranteed to match a fine-"
                      "grained identity or relationship; inspect alternatives "
                      "against the image before acting. Feed the selected u,v "
                      "to unproject_pixel for world XYZ.")},
            obs)
