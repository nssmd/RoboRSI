"""Attach an on-demand orbit RGB-D view or labeled contact sheet."""

from __future__ import annotations

from typing import Any

import cv2

from roborsi.embodied.sim.libero.orbit_geometry import compose_orbit_sheet
from roborsi.embodied.skills.base._lib.libero._perception import (
    write_image_atomic,
)


def dispatch_runtime(state, args: dict[str, Any]):
    requested_size = args.get("image_size", 512)
    try:
        image_size = int(requested_size)
    except (TypeError, ValueError):
        image_size = 512
    image_size = max(256, min(512, image_size))
    frames = state.env.capture_orbit_views(image_size=image_size)
    if not frames:
        return (
            {"ok": False, "reason": "orbit views unavailable"},
            state.env.take_snapshot(),
        )
    requested = str(args.get("view") or "").strip()
    if requested:
        frame = frames.get(requested)
        if frame is None:
            return (
                {
                    "ok": False,
                    "reason": f"unknown orbit view: {requested}",
                    "available": sorted(frames),
                },
                state.env.take_snapshot(),
            )
        image = frame.rgb
        selected = [requested]
    else:
        selected = list(frames)
        image = compose_orbit_sheet(
            [frames[name] for name in selected],
            tile_size=min(384, image_size),
        )
    sequence = int(getattr(state, "_orbit_view_seq", 0)) + 1
    setattr(state, "_orbit_view_seq", sequence)
    path = state.workdir / f"orbit_view_{sequence:04d}.png"
    write_image_atomic(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    state.last_image_path = path
    return (
        {
            "ok": True,
            "views": selected,
            "image_size": image_size,
            "note": "orbit image attached; select one view before marking a pixel",
        },
        state.env.take_snapshot(),
    )
