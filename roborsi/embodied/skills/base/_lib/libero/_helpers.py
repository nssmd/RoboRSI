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
