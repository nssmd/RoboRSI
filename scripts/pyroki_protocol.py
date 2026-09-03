"""Dependency-free validation for the PyRoKi ZMQ wire contract."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

WIRE_PROTOCOL = "roborsi.pyroki.live_joints.v1"


def validate_arm_joints(value: Any, *, field: str) -> list[float]:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 7
    ):
        raise ValueError(f"{field} must contain exactly 7 finite numbers")
    joints: list[float] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"{field} must contain exactly 7 finite numbers")
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"{field} must contain exactly 7 finite numbers"
            ) from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must contain exactly 7 finite numbers")
        joints.append(number)
    return joints


def trajectory_start_error(
    trajectory: Iterable[Sequence[float]],
    start_joints: Sequence[float],
) -> float:
    expected = validate_arm_joints(start_joints, field="start_joints")
    rows = list(trajectory)
    if not rows:
        raise ValueError("trajectory must contain at least one 7-joint waypoint")
    try:
        first = validate_arm_joints(rows[0], field="trajectory first waypoint")
    except ValueError as exc:
        raise ValueError(
            "trajectory must contain finite 7-joint waypoints"
        ) from exc
    return max(abs(actual - target) for actual, target in zip(first, expected))
