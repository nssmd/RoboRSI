"""Shared camera helpers for base/libero skills."""

from __future__ import annotations

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


def classify_gripper_gap(gap: float | None) -> str:
    """Classify Panda finger spacing without reading object state."""
    if gap is None:
        return "unknown"
    if gap <= 0.01:
        return "closed_empty"
    if gap >= 0.075:
        return "open"
    return "holding"
