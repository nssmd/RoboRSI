"""rotate_vector — rotate a vector about x/y/z by degrees (base/libero)."""

from __future__ import annotations

from typing import Any

import numpy as np

_AXES = {
    "x": lambda c, s: np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
    "y": lambda c, s: np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
    "z": lambda c, s: np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]),
}


def dispatch_runtime(state, args: dict[str, Any]):
    vec = args.get("vector")
    axis = str(args.get("axis") or "z").lower()
    if not isinstance(vec, (list, tuple)) or len(vec) not in (2, 3) or axis not in _AXES:
        return ({"ok": False, "reason": "vector must be 2D/3D and axis in x/y/z"},
                state.env.take_snapshot())
    v = np.zeros(3)
    v[:len(vec)] = np.asarray(vec, dtype=float)
    ang = np.deg2rad(float(args.get("angle_deg", 0.0)))
    r = _AXES[axis](np.cos(ang), np.sin(ang)) @ v
    out = r[:len(vec)]
    return ({"ok": True, "rotated": [round(float(x), 5) for x in out]},
            state.env.take_snapshot())
