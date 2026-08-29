"""measure_distance — Euclidean distance + delta between two points (base/libero)."""

from __future__ import annotations

from typing import Any

import numpy as np


def dispatch_runtime(state, args: dict[str, Any]):
    p1 = args.get("p1")
    p2 = args.get("p2")
    if not isinstance(p1, (list, tuple)) or not isinstance(p2, (list, tuple)) \
            or len(p1) != len(p2):
        return ({"ok": False, "reason": "p1 and p2 must be equal-length lists"},
                state.env.take_snapshot())
    a = np.asarray(p1, dtype=float)
    b = np.asarray(p2, dtype=float)
    delta = b - a
    return ({"ok": True,
             "distance": round(float(np.linalg.norm(delta)), 5),
             "delta": [round(float(v), 5) for v in delta]},
            state.env.take_snapshot())
