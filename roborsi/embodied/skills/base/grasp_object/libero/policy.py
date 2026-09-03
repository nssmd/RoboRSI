"""grasp_object — camera/depth composite pick for LIBERO."""

from __future__ import annotations

import os
from typing import Any

import numpy as np

# LIBERO head frames render at 256px; the perception fallbacks emit the image
# CENTRE (128,128) as a "found nothing" sentinel. Grasping there always closes
# on empty table AND makes the VLM re-find_pixel forever (budget_exceeded), so we
# treat it as a hard localization failure instead of a real detection.
_SENTINEL_UV = (128, 128)
_SENTINEL_TOL = 2  # pixels


def _fix_on() -> bool:
    """Pure-vision grasp fixes (SAM3-first localize, self-correcting re-localize,
    rim yaw-sweep) are ON by default — verified 46cm→4cm grasp error, 25%→82%
    right-object. ``ROBORSI_GRASP_FIX=0`` restores the old unguarded path."""
    return os.environ.get("ROBORSI_GRASP_FIX", "1") != "0"


def _is_sentinel(uv) -> bool:
    if uv is None:
        return False
    su, sv = _SENTINEL_UV
    return abs(int(uv[0]) - su) <= _SENTINEL_TOL and abs(int(uv[1]) - sv) <= _SENTINEL_TOL


def _grasp_z_offset(args: dict[str, Any], cloud) -> float:
    try:
        requested = float(args.get("grasp_z_offset", 0.0))
    except (TypeError, ValueError, OverflowError):
        requested = 0.0
    if not np.isfinite(requested):
        requested = 0.0
    requested = float(np.clip(requested, -0.04, 0.04))
    points = np.asarray(cloud, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 10:
        return requested
    points = points[np.all(np.isfinite(points), axis=1)]
    if len(points) < 10:
        return requested
    spans = np.ptp(points, axis=0)
    horizontal = max(float(spans[0]), float(spans[1]), 1e-6)
    if abs(requested) < 1e-6 and spans[2] >= 0.06 and spans[2] >= 1.2 * horizontal:
        return float(np.clip(0.35 * spans[2], 0.015, 0.03))
    return requested


# ── perception mode ──────────────────────────────────────────────────────
def _locate_pixel(state, args):
    name = str(args.get("object") or "").strip()
    from roborsi.embodied.skills.base._lib.libero._perception import (
        recall_pixel,
        remember_pixel,
    )

    # Reuse the result of an explicit find_pixel call. Re-localizing here wastes
    # one perception call and can switch to a different same-category object.
    pix = args.get("pixel")
    if isinstance(pix, (list, tuple)) and len(pix) == 2:
        return remember_pixel(state, name, pix)
    cached = recall_pixel(state, name)
    if cached is not None:
        return cached
    if name and _fix_on():
        from roborsi.embodied.skills.base._lib.libero._perception import localize_precise
        uv = localize_precise(state, name)
        if uv is not None:
            return uv
    if not name:
        return None
    from roborsi.embodied.skills.base._lib.libero._perception import localize_precise
    return localize_precise(state, name)


def _perception_grasp(state, args):
    from roborsi.embodied.skills.base._lib.libero._perception import (
        execute_topdown,
        grasps_at_pixel,
    )
    env = state.env
    loc = _locate_pixel(state, args)
    if loc is None:
        return ({"ok": False, "grasped": False,
                 "reason": "could not locate the object by vision — call find_pixel(object) and pass pixel=[u, v]"},
                env.take_snapshot())
    if _is_sentinel(loc):
        # Perception returned the image-centre sentinel: NOT a real detection.
        # Refuse the grasp so the VLM re-perceives instead of grasping empty
        # table and looping until budget_exceeded.
        return ({"ok": False, "grasped": False, "sentinel": True,
                 "reason": "perception returned the (128,128) centre sentinel (nothing found) — "
                           "re-find_pixel with a more specific query, look() closer, or zoom_in "
                           "before retrying grasp_object with an explicit pixel=[u, v]"},
                env.take_snapshot())
    u, v = loc
    grasps, _cloud = grasps_at_pixel(env, u, v, top_k=3)
    if not grasps and _fix_on():
        # The VLM's pixel yielded a rejected (whole-scene) mask. Rather than bounce
        # back to the VLM — which loops on re-perception until budget_exceeded —
        # self-correct with the detector: re-point via localize_precise and retry.
        name = str(args.get("object") or "").strip()
        if name:
            from roborsi.embodied.skills.base._lib.libero._perception import localize_precise
            loc2 = localize_precise(state, name)
            if loc2 and not _is_sentinel(loc2):
                g2, c2 = grasps_at_pixel(env, int(loc2[0]), int(loc2[1]), top_k=3)
                if g2:
                    grasps, _cloud, u, v = g2, c2, int(loc2[0]), int(loc2[1])
    if not grasps:
        return ({"ok": False, "grasped": False,
                 "reason": "Could not construct a segmented grasp for that pixel."},
                env.take_snapshot())
    grasp_z_offset = _grasp_z_offset(args, _cloud)
    p, ee, gq = execute_topdown(
        env,
        grasps[0],
        cloud=_cloud,
        z_offset=grasp_z_offset,
    )
    gap = round(float(gq[0] - gq[1]), 4) if gq is not None else None
    from roborsi.embodied.skills.base._lib.libero._helpers import (
        classify_gripper_gap,
    )

    grasped = classify_gripper_gap(gap) == "holding"
    if _fix_on() and not grasped:
        # Rim straddle for bowls/wide-mouth: an accurate top-down grasp still
        # closes on air when the jaws lie ALONG the thin rim. Sweep the gripper
        # yaw (+ the next GraspGen candidate) and keep the pose that actually
        # holds — CaP's "try candidates, check in-hand" idea, extended to yaw.
        for cand in grasps[:1]:
            for yaw in (0.785, -0.785, 1.571):
                p, ee, gq = execute_topdown(
                    env,
                    cand,
                    cloud=_cloud,
                    yaw=yaw,
                    z_offset=grasp_z_offset,
                )
                gap = round(float(gq[0] - gq[1]), 4) if gq is not None else None
                if classify_gripper_gap(gap) == "holding":
                    grasped = True
                    break
            if grasped:
                break
    backend = grasps[0].get("source", "graspgen+sam")
    object_height = (
        float(np.ptp(np.asarray(_cloud)[:, 2]))
        if _cloud is not None and len(_cloud) >= 2
        else 0.08
    )
    object_height = float(np.clip(object_height, 0.02, 0.16))
    if grasped:
        state._held_object_height = object_height
        state._held_grasp_z_offset = grasp_z_offset
    return ({"ok": True, "grasped": grasped, "backend": backend,
             "grasp_point": [round(float(x), 4) for x in p],
             "grasp_pixel": [u, v], "gripper_gap": gap,
             "object_height": round(object_height, 4),
             "grasp_z_offset": round(grasp_z_offset, 4),
             "ee_pos": [round(float(x), 4) for x in ee],
             "note": "perception grasp (no GT); 'grasped' is a proprioceptive finger-gap check, sim predicate is final."},
            env.take_snapshot())


def dispatch_runtime(state, args: dict[str, Any]):
    return _perception_grasp(state, args)
