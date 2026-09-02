"""measure_relative_rotation — signed angle v1→v2 about an axis (base/libero)."""

from __future__ import annotations

from typing import Any

import numpy as np

_AXIS_IDX = {"x": 0, "y": 1, "z": 2}


def dispatch_runtime(state, args: dict[str, Any]):
    v1 = args.get("v1")
    v2 = args.get("v2")
    axis = str(args.get("axis") or "z").lower()
    if not isinstance(v1, (list, tuple)) or not isinstance(v2, (list, tuple)) \
            or axis not in _AXIS_IDX:
        return ({"ok": False, "reason": "v1, v2 lists and axis in x/y/z required"},
                state.env.take_snapshot())
    i, j = [k for k in range(3) if k != _AXIS_IDX[axis]]
    a = np.zeros(3); a[:len(v1)] = np.asarray(v1, dtype=float)
    b = np.zeros(3); b[:len(v2)] = np.asarray(v2, dtype=float)
    ang = np.arctan2(b[j], b[i]) - np.arctan2(a[j], a[i])
    ang = (np.rad2deg(ang) + 180.0) % 360.0 - 180.0
    return ({"ok": True, "angle_deg": round(float(ang), 3)},
            state.env.take_snapshot())
