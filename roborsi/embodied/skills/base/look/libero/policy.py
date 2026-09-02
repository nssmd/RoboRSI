"""look — snap a camera frame and attach it to the next VLM turn (base/libero).

Writes a JPEG to the episode workdir and sets ``state.last_image_path``; the
rollout loop appends that image to the next user message so the VLM can see it.
"""

from __future__ import annotations

from typing import Any

import cv2

# head → head_camera (agentview), wrist → wrist (eye-in-hand), in Observation.images.
_IMAGE_KEY = {"head": "head_camera", "wrist": "wrist",
              "agentview": "head_camera", "robot0_eye_in_hand": "wrist"}


def dispatch_runtime(state, args: dict[str, Any]):
    which = str(args.get("camera") or "head").strip().lower()
    key = _IMAGE_KEY.get(which, "head_camera")
    obs = state.env.take_snapshot()
    rgb = obs.images.get(key)
    if rgb is None:
        return ({"ok": False, "reason": f"no camera '{which}'",
                 "available": list(obs.images)}, obs)
    path = state.workdir / f"look_{key}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    state.last_image_path = path
    return ({"ok": True, "camera": key, "note": "image attached to next turn"}, obs)
