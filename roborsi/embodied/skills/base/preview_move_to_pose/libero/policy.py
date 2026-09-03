"""Preview a reachable LIBERO move and mint a state-bound execution token."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import cv2
import numpy as np

from roborsi.embodied.sim.libero.orbit_geometry import compose_orbit_sheet
from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero._perception import (
    write_image_atomic,
)


def _finite_vector(value: Any, size: int) -> np.ndarray | None:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result.shape == (size,) and np.all(np.isfinite(result)) else None


def _project(frame, point: np.ndarray) -> tuple[int, int] | None:
    local = frame.camera_to_world_rotation.T @ (
        point - frame.camera_position_world
    )
    if not np.all(np.isfinite(local)) or float(local[2]) <= 1e-6:
        return None
    u = int(round(frame.fx * float(local[0]) / float(local[2]) + frame.cx))
    v = int(round(frame.fy * float(local[1]) / float(local[2]) + frame.cy))
    height, width = frame.rgb.shape[:2]
    return (u, v) if 0 <= u < width and 0 <= v < height else None


def dispatch_runtime(state, args: dict[str, Any]):
    position = _finite_vector(args.get("pos"), 3)
    if position is None:
        return (
            {"ok": False, "reachable": False, "reason": "pos must be finite [x,y,z]"},
            state.env.take_snapshot(),
        )
    quaternion = args.get("quat")
    if quaternion is not None:
        quaternion = _finite_vector(quaternion, 4)
        norm = float(np.linalg.norm(quaternion)) if quaternion is not None else 0.0
        if quaternion is None or norm <= 1e-12:
            return (
                {"ok": False, "reachable": False, "reason": "quat must be finite and non-zero"},
                state.env.take_snapshot(),
            )
        quaternion = quaternion / norm
    gripper = str(args.get("gripper") or "keep").strip().lower()
    if gripper not in {"open", "close", "keep"}:
        return (
            {"ok": False, "reachable": False, "reason": "invalid gripper command"},
            state.env.take_snapshot(),
        )
    control = LiberoControl(state.env)
    ee_pos, _, _ = control.read_pose()
    goal = control.preview_goal_config(position, quaternion)
    if goal is None:
        return (
            {
                "ok": True,
                "reachable": False,
                "reason": "IK found no reachable goal configuration",
                "target_pos": position.tolist(),
            },
            state.env.take_snapshot(),
        )
    trajectory = control.preview_trajectory(position, quaternion)
    if trajectory is None:
        return (
            {
                "ok": True,
                "reachable": False,
                "reason": "trajectory planner found no branch-continuous path",
                "target_pos": position.tolist(),
            },
            state.env.take_snapshot(),
        )
    frames = state.env.capture_orbit_views(image_size=512)
    annotated = []
    for frame in frames.values():
        image = np.asarray(frame.rgb, dtype=np.uint8).copy()
        pixel = _project(frame, position)
        if pixel is not None:
            cv2.drawMarker(
                image,
                pixel,
                (64, 255, 64),
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=22,
                thickness=2,
            )
        annotated.append(replace(frame, rgb=image))
    sequence = int(getattr(state, "_move_preview_seq", 0)) + 1
    setattr(state, "_move_preview_seq", sequence)
    preview_id = f"move-preview-{sequence:04d}"
    path = state.workdir / f"{preview_id}.png"
    sheet = compose_orbit_sheet(annotated, tile_size=384)
    write_image_atomic(path, cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    state.last_image_path = path
    previews = getattr(state, "_libero_move_previews", None)
    if not isinstance(previews, dict):
        previews = {}
        setattr(state, "_libero_move_previews", previews)
    move_args = {
        "pos": position.tolist(),
        "gripper": gripper,
        "max_iters": int(args.get("max_iters") or 80),
        "via_trajopt": True,
    }
    if quaternion is not None:
        move_args["quat"] = quaternion.tolist()
    previews[preview_id] = {
        "args": move_args,
        "generation": int(state.env.orbit_generation()),
        "ee_pos": np.asarray(ee_pos, dtype=float).tolist(),
        "trajectory": np.asarray(trajectory, dtype=float).tolist(),
    }
    return (
        {
            "ok": True,
            "reachable": True,
            "preview_id": preview_id,
            "target_pos": position.tolist(),
            "current_ee_pos": np.asarray(ee_pos, dtype=float).tolist(),
            "trajectory_waypoints": int(np.asarray(trajectory).shape[0]),
            "views": list(frames),
            "note": "trajectory-backed preview attached; execute only with execute_previewed_move",
        },
        state.env.take_snapshot(),
    )
