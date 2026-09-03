"""Back-project one pixel from a fresh orbit RGB-D frame."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from roborsi.embodied.sim.libero.orbit_geometry import triangulate_rays
from roborsi.embodied.skills.base._lib.libero._perception import (
    write_image_atomic,
)


def _attach_marker(state, frame, *, view: str, u: int, v: int) -> None:
    annotated = np.asarray(frame.rgb, dtype=np.uint8).copy()
    cv2.drawMarker(
        annotated,
        (u, v),
        (255, 64, 64),
        markerType=cv2.MARKER_CROSS,
        markerSize=18,
        thickness=2,
    )
    sequence = int(getattr(state, "_orbit_mark_seq", 0)) + 1
    setattr(state, "_orbit_mark_seq", sequence)
    path = state.workdir / f"orbit_mark_{view}_{sequence:04d}.png"
    write_image_atomic(path, cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))
    state.last_image_path = path


def dispatch_runtime(state, args: dict[str, Any]):
    view = str(args.get("view") or "").strip()
    try:
        u, v = int(args.get("u")), int(args.get("v"))
    except (TypeError, ValueError):
        return (
            {"ok": False, "reason": "view, u, and v are required"},
            state.env.take_snapshot(),
        )
    frame = state.env.orbit_frame(view)
    if frame is None:
        return (
            {"ok": False, "reason": "orbit view missing or stale"},
            state.env.take_snapshot(),
        )
    mode = str(args.get("mode") or "surface").strip().lower()
    if mode == "ray":
        try:
            origin, direction = frame.ray_at(u, v)
        except ValueError as exc:
            return (
                {"ok": False, "reason": str(exc)},
                state.env.take_snapshot(),
            )
        generation = int(getattr(state.env, "orbit_generation", lambda: 0)())
        pending = getattr(state, "_orbit_pending_rays", None)
        if not isinstance(pending, dict):
            pending = {}
            setattr(state, "_orbit_pending_rays", pending)
        point_id = str(args.get("point_id") or "").strip()
        if not point_id:
            sequence = int(getattr(state, "_orbit_point_seq", 0)) + 1
            setattr(state, "_orbit_point_seq", sequence)
            point_id = f"orbit-point-{sequence:04d}"
            pending[point_id] = {
                "origin": origin,
                "direction": direction,
                "view": view,
                "generation": generation,
            }
            _attach_marker(state, frame, view=view, u=u, v=v)
            return (
                {
                    "ok": True,
                    "complete": False,
                    "point_id": point_id,
                    "first_view": view,
                    "need": "click the same free-space point in a different orbit view",
                },
                state.env.take_snapshot(),
            )
        first = pending.get(point_id)
        if not isinstance(first, dict):
            return (
                {"ok": False, "reason": "unknown or consumed point_id"},
                state.env.take_snapshot(),
            )
        if int(first.get("generation", -1)) != generation:
            pending.pop(point_id, None)
            return (
                {"ok": False, "reason": "orbit views changed; start a new point"},
                state.env.take_snapshot(),
            )
        if str(first.get("view")) == view:
            return (
                {"ok": False, "reason": "second ray must use a different view"},
                state.env.take_snapshot(),
            )
        solved = triangulate_rays(
            first["origin"],
            first["direction"],
            origin,
            direction,
        )
        if solved is None or solved[1] > 0.05:
            return (
                {"ok": False, "reason": "rays do not identify one consistent point"},
                state.env.take_snapshot(),
            )
        pending.pop(point_id, None)
        _attach_marker(state, frame, view=view, u=u, v=v)
        return (
            {
                "ok": True,
                "complete": True,
                "point_id": point_id,
                "world": [float(value) for value in solved[0]],
                "ray_separation_m": float(solved[1]),
                "source": "orbit_two_ray_triangulation",
            },
            state.env.take_snapshot(),
        )
    if mode != "surface":
        return (
            {"ok": False, "reason": "mode must be surface or ray"},
            state.env.take_snapshot(),
        )
    world = state.env.orbit_pixel_to_world(view, u, v)
    if world is None:
        return (
            {"ok": False, "reason": "pixel has no valid visible depth"},
            state.env.take_snapshot(),
        )
    _attach_marker(state, frame, view=view, u=u, v=v)
    return (
        {
            "ok": True,
            "view": view,
            "pixel": [u, v],
            "world": [float(value) for value in world],
            "source": "orbit_rgbd_surface",
        },
        state.env.take_snapshot(),
    )
