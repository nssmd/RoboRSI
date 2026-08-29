"""Camera-depth 6-DoF grasp candidates for LIBERO."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._helpers import parse_image_pixel
from roborsi.embodied.skills.base._lib.libero._perception import (
    graspgen_to_eef_quat,
    grasps_at_pixel,
)


def _pixel(state: Any, args: dict[str, Any]) -> tuple[int, int] | None:
    value = args.get("pixel")
    if value is None:
        value = [args.get("u"), args.get("v")]
    image = state.env.take_snapshot().images.get("head_camera")
    return parse_image_pixel(value, image)


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    uv = _pixel(state, args)
    if uv is None:
        return (
            {"ok": False, "reason": "give a finite in-frame head pixel [u,v]"},
            env.take_snapshot(),
        )
    try:
        top_k = int(args.get("top_k", 3))
    except (TypeError, ValueError, OverflowError):
        top_k = 3
    top_k = max(1, min(10, top_k))
    grasps, _ = grasps_at_pixel(env, uv[0], uv[1], top_k=top_k)
    if not grasps:
        return (
            {
                "ok": False,
                "reason": "no camera-depth grasp candidates at that pixel",
            },
            env.take_snapshot(),
        )
    rows = []
    for grasp in grasps:
        try:
            score = float(grasp.get("score"))
            approach_z = float(grasp.get("approach_z"))
        except (TypeError, ValueError, OverflowError):
            continue
        rotation = np.asarray(
            grasp.get("rotation_matrix_world"),
            dtype=float,
        )
        position = np.asarray(
            grasp.get("translation_tcp_world"),
            dtype=float,
        )
        if (
            rotation.shape != (3, 3)
            or position.shape != (3,)
            or not np.all(np.isfinite(rotation))
            or not np.all(np.isfinite(position))
            or not np.isfinite(score)
            or not np.isfinite(approach_z)
        ):
            continue
        quat = np.asarray(graspgen_to_eef_quat(rotation), dtype=float)
        if quat.shape != (4,) or not np.all(np.isfinite(quat)):
            continue
        rows.append(
            {
                "score": round(score, 3),
                "pos": [round(float(value), 4) for value in position],
                "quat": [round(float(value), 6) for value in quat],
                "approach_z": round(approach_z, 3),
            }
        )
    if not rows:
        return (
            {"ok": False, "reason": "grasp candidates lacked finite 6-DoF poses"},
            env.take_snapshot(),
        )
    return (
        {"ok": True, "count": len(rows), "grasps": rows},
        env.take_snapshot(),
    )
