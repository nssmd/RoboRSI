"""Shared camera helpers for base/libero skills."""

from __future__ import annotations

from typing import Any

import numpy as np

# head = agentview overview, wrist = eye-in-hand close-up.
_CAMERA_ALIASES = {
    "head": "agentview",
    "wrist": "robot0_eye_in_hand",
    "agentview": "agentview",
    "robot0_eye_in_hand": "robot0_eye_in_hand",
}


def resolve_camera(alias: str | None) -> str:
    """Map a camera alias (head/wrist/…) to its robosuite obs-key base name."""
    return _CAMERA_ALIASES.get(str(alias or "head").strip().lower(), "agentview")


def parse_image_pixel(
    value: Any,
    image: Any,
    *,
    fallback_hw: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """Return an in-frame integer ``(u, v)`` without lossy coercion."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, (bool, np.bool_)) for item in value):
        return None
    try:
        coords = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if coords.shape != (2,) or not np.all(np.isfinite(coords)):
        return None
    if not np.all(coords == np.floor(coords)):
        return None
    if image is not None:
        try:
            array = np.asarray(image)
        except (TypeError, ValueError, OverflowError):
            return None
        if array.ndim < 2:
            return None
        height, width = array.shape[:2]
    elif fallback_hw is not None and len(fallback_hw) == 2:
        height, width = int(fallback_hw[0]), int(fallback_hw[1])
    else:
        return None
    if not (0 <= coords[0] < width and 0 <= coords[1] < height):
        return None
    return int(coords[0]), int(coords[1])


def classify_gripper_gap(gap: float | None) -> str:
    """Classify Panda finger spacing without reading object state."""
    if gap is None:
        return "unknown"
    if gap <= 0.01:
        return "closed_empty"
    if gap >= 0.075:
        return "open"
    return "holding"
