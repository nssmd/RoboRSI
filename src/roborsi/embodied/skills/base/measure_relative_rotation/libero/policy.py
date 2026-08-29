"""measure_relative_rotation — signed angle v1→v2 about an axis (base/libero)."""

from __future__ import annotations

from typing import Any

import numpy as np

_AXIS_VECTOR = {
    "x": np.array([1.0, 0.0, 0.0]),
    "y": np.array([0.0, 1.0, 0.0]),
    "z": np.array([0.0, 0.0, 1.0]),
}


def dispatch_runtime(state, args: dict[str, Any]):
    v1 = args.get("v1")
    v2 = args.get("v2")
    axis = str(args.get("axis") or "z").lower()
    if (
        not isinstance(v1, (list, tuple))
        or not isinstance(v2, (list, tuple))
        or len(v1) not in (2, 3)
        or len(v2) not in (2, 3)
        or axis not in _AXIS_VECTOR
        or any(isinstance(value, (bool, np.bool_)) for value in (*v1, *v2))
    ):
        return ({"ok": False, "reason": "v1, v2 lists and axis in x/y/z required"},
                state.env.take_snapshot())
    try:
        a = np.zeros(3)
        b = np.zeros(3)
        a[:len(v1)] = np.asarray(v1, dtype=float)
        b[:len(v2)] = np.asarray(v2, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return ({"ok": False, "reason": "vectors must contain finite numbers"},
                state.env.take_snapshot())
    axis_vector = _AXIS_VECTOR[axis]
    a = a - axis_vector * float(np.dot(a, axis_vector))
    b = b - axis_vector * float(np.dot(b, axis_vector))
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if (
        not np.all(np.isfinite(a))
        or not np.all(np.isfinite(b))
        or a_norm <= np.finfo(float).eps
        or b_norm <= np.finfo(float).eps
    ):
        return ({"ok": False, "reason": "vectors must project non-zero onto the rotation plane"},
                state.env.take_snapshot())
    a /= a_norm
    b /= b_norm
    angle_rad = np.arctan2(
        float(np.dot(axis_vector, np.cross(a, b))),
        float(np.dot(a, b)),
    )
    ang = np.rad2deg(angle_rad)
    return ({"ok": True, "angle_deg": round(float(ang), 3)},
            state.env.take_snapshot())
