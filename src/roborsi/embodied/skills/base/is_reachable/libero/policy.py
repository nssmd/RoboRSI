"""Conservative finite-coordinate workspace check for LIBERO."""

from __future__ import annotations

from typing import Any

import numpy as np

_REACH_MAX = 0.85
_REACH_MIN = 0.15
_Z_REL_MIN = -0.15
_Z_REL_MAX = 0.55


def _base_position(env: Any) -> np.ndarray | None:
    try:
        base = np.asarray(env.robot_base_pos(), dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if base.shape != (3,) or not np.all(np.isfinite(base)):
        return None
    return base


def _failure(reason: str, base: np.ndarray | None) -> dict[str, Any]:
    return {
        "ok": False,
        "reachable": False,
        "distance_to_base": None,
        "base_pos": (
            [round(float(value), 4) for value in base]
            if base is not None
            else None
        ),
        "reason": reason,
    }


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    base = _base_position(env)
    if base is None:
        return _failure("robot base pose unavailable", None), env.take_snapshot()

    value = args.get("pos")
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        value = [args.get("x"), args.get("y"), args.get("z")]
    if any(isinstance(item, (bool, np.bool_)) for item in value):
        return _failure("target must be finite [x,y,z]", base), env.take_snapshot()
    try:
        target = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        target = np.array([], dtype=float)
    if target.shape != (3,) or not np.all(np.isfinite(target)):
        return _failure("target must be finite [x,y,z]", base), env.take_snapshot()

    with np.errstate(over="ignore", invalid="ignore"):
        distance = float(np.linalg.norm(target - base))
    if not np.isfinite(distance):
        return _failure("target distance is outside numeric range", base), env.take_snapshot()
    z_relative = float(target[2] - base[2])
    reasons: list[str] = []
    if distance > _REACH_MAX:
        reasons.append(f"{distance:.3f}m from base exceeds {_REACH_MAX:.2f}m")
    if distance < _REACH_MIN:
        reasons.append(f"{distance:.3f}m from base is below {_REACH_MIN:.2f}m")
    if not (_Z_REL_MIN <= z_relative <= _Z_REL_MAX):
        reasons.append(
            f"relative z {z_relative:.3f}m outside "
            f"[{_Z_REL_MIN:.2f},{_Z_REL_MAX:.2f}]"
        )
    reachable = not reasons
    return (
        {
            "ok": True,
            "reachable": reachable,
            "distance_to_base": round(distance, 4),
            "base_pos": [round(float(value), 4) for value in base],
            "reason": "within workspace envelope" if reachable else "; ".join(reasons),
        },
        env.take_snapshot(),
    )
