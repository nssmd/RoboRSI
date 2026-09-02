"""measure_vector — p1→p2 vector, length, unit direction (base/libero)."""

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
    v = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    length = float(np.linalg.norm(v))
    unit = (v / length) if length > 1e-9 else v
    return ({"ok": True,
             "vector": [round(float(x), 5) for x in v],
             "length": round(length, 5),
             "unit": [round(float(x), 5) for x in unit]},
            state.env.take_snapshot())
