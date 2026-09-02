"""Shared helpers for base/libero skills (kept out of _control.py — no sim state).

Consolidates small logic that was otherwise copy-pasted across skills: listing
the scene's manipulable objects from a robosuite obs dict, and resolving a
human-friendly camera alias (head/wrist) to its robosuite key.
"""

from __future__ import annotations

from typing import Any

# head = agentview overview, wrist = eye-in-hand close-up.
_CAMERA_ALIASES = {
    "head": "agentview",
    "wrist": "robot0_eye_in_hand",
    "agentview": "agentview",
    "robot0_eye_in_hand": "robot0_eye_in_hand",
}


def scene_object_names(obs: dict[str, Any]) -> list[str]:
    """Manipulable objects in the scene = obs keys ``<name>_pos`` (with a
    matching ``_quat``) that are neither the robot's end-effector nor a relative
    ``_to_robot0_eef`` measurement."""
    return sorted(
        k[:-4] for k in obs
        if k.endswith("_pos") and not k.startswith("robot0")
        and not k[:-4].endswith("_to_robot0_eef")
        and f"{k[:-4]}_quat" in obs
    )


def resolve_camera(alias: str | None) -> str:
    """Map a camera alias (head/wrist/…) to its robosuite obs-key base name."""
    return _CAMERA_ALIASES.get(str(alias or "head").strip().lower(), "agentview")
