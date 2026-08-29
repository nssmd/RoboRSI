"""Pure-vision bounded planar push between fresh source and target pixels."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero._helpers import parse_image_pixel
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base._lib.libero.semantic_point import (
    matching_semantic_point,
)

_MIN_PLANAR_DISTANCE = 0.03
_MAX_REQUIRED_PUSH_DISTANCE = 0.08
_MIN_CONTACT_BACKOFF = 0.01
_MAX_CONTACT_BACKOFF = 0.045
_CONTACT_EDGE_PADDING = 0.002
_MIN_HOVER_STANDOFF = 0.02
_MAX_SOURCE_TO_HOVER_BACKOFF = 0.09
_MAX_PUSH_SEGMENT_DISTANCE = 0.02


def _bounded(value: Any, default: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if not np.isfinite(result):
        result = default
    return float(np.clip(result, low, high))


def _world_point(env: Any, pixel: tuple[int, int]) -> np.ndarray | None:
    value = env.pixel_to_world(int(pixel[0]), int(pixel[1]))
    try:
        point = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    return point if point.shape == (3,) and np.all(np.isfinite(point)) else None


def _source_semantic_current(
    env: Any,
    object_name: str,
    pixel: tuple[int, int],
    frame: np.ndarray,
) -> bool:
    return matching_semantic_point(
        env,
        object_name=object_name,
        pixel=pixel,
        current_frame=frame,
    ) is not None


def _visual_contact_backoff(
    env: Any,
    source_pixel: tuple[int, int],
    source: np.ndarray,
    direction: np.ndarray,
) -> float:
    from roborsi.embodied.skills.base._lib.libero._perception import (
        object_cloud,
    )

    try:
        cloud = object_cloud(
            env,
            int(source_pixel[0]),
            int(source_pixel[1]),
            z_band=0.08,
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        return _MIN_CONTACT_BACKOFF
    try:
        points = np.asarray(cloud, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return _MIN_CONTACT_BACKOFF
    if points.ndim != 2 or points.shape[1] != 3:
        return _MIN_CONTACT_BACKOFF
    points = points[np.all(np.isfinite(points), axis=1)]
    if len(points) < 8:
        return _MIN_CONTACT_BACKOFF
    projected = (points[:, :2] - np.asarray(source[:2], dtype=float)) @ direction
    back_extent = max(0.0, -float(np.percentile(projected, 15)))
    return float(
        np.clip(
            back_extent + _CONTACT_EDGE_PADDING,
            _MIN_CONTACT_BACKOFF,
            _MAX_CONTACT_BACKOFF,
        )
    )


def _canonical_topdown_push_quat(
    direction: np.ndarray,
    current_quat: np.ndarray,
) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    planar = np.asarray(direction, dtype=float)
    jaw = np.array([-planar[1], planar[0], 0.0], dtype=float)
    jaw /= float(np.linalg.norm(jaw))
    approach = np.array([0.0, 0.0, -1.0], dtype=float)

    def _quat(jaw_axis: np.ndarray) -> np.ndarray:
        x_axis = np.cross(jaw_axis, approach)
        matrix = np.column_stack([x_axis, jaw_axis, approach])
        return Rotation.from_matrix(matrix).as_quat()

    candidates = [_quat(jaw), _quat(-jaw)]
    current = np.asarray(current_quat, dtype=float)
    norm = float(np.linalg.norm(current))
    if current.shape == (4,) and np.all(np.isfinite(current)) and norm > 0.0:
        current /= norm
        return max(
            candidates,
            key=lambda candidate: abs(float(np.dot(candidate, current))),
        )
    return candidates[0]


def _failure(env: Any, reason: str, **fields: Any):
    return (
        {"ok": False, "pushed": False, "reason": reason, **fields},
        env.take_snapshot(),
    )


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    object_name = str(args.get("object") or "").strip()
    if not object_name:
        return _failure(env, "object must name the pushed object")
    snapshot = env.take_snapshot()
    frame = snapshot.images.get("head_camera")
    if frame is None:
        return _failure(env, "head-camera image unavailable")
    frame = np.asarray(frame)
    source_pixel = parse_image_pixel(args.get("source_pixel"), frame)
    target_pixel = parse_image_pixel(args.get("target_pixel"), frame)
    if source_pixel is None or target_pixel is None:
        return _failure(env, "source_pixel and target_pixel must be finite in-frame pixels")
    if not _source_semantic_current(env, object_name, source_pixel, frame):
        return _failure(env, "current source semantic point is missing or stale")
    source = _world_point(env, source_pixel)
    target = _world_point(env, target_pixel)
    if source is None or target is None:
        return _failure(env, "source or target depth is unavailable")
    planar = np.asarray(target[:2] - source[:2], dtype=float)
    planar_distance = float(np.linalg.norm(planar))
    if planar_distance < _MIN_PLANAR_DISTANCE:
        return _failure(env, "source and target must be visibly separated")
    direction = planar / planar_distance

    control = LiberoControl(env)
    gap, gripper_state = control.read_gripper_state()
    if gripper_state is GripperState.HELD:
        return _failure(
            env,
            "refusing planar push while the gripper is holding",
            gripper_gap=round(float(gap), 4),
        )
    if gripper_state is GripperState.OPEN:
        control.set_gripper(close=True)

    max_distance = _bounded(args.get("max_distance"), 0.20, 0.015, 0.25)
    push_distance = min(planar_distance, max_distance)
    standoff = _bounded(args.get("standoff"), 0.06, 0.03, 0.10)
    hover_clearance = _bounded(
        args.get("hover_clearance"), 0.08, 0.04, 0.14
    )
    contact_z_offset = _bounded(
        args.get("contact_z_offset"), -0.005, -0.02, 0.05
    )
    xy_direction = np.array([direction[0], direction[1], 0.0], dtype=float)
    contact_backoff = _visual_contact_backoff(
        env,
        source_pixel,
        source,
        direction,
    )
    contact = np.asarray(source, dtype=float) - xy_direction * contact_backoff
    contact[2] = float(source[2]) + contact_z_offset
    effective_standoff = min(
        standoff,
        max(
            _MIN_HOVER_STANDOFF,
            _MAX_SOURCE_TO_HOVER_BACKOFF - contact_backoff,
        ),
    )
    hover = np.asarray(contact, dtype=float).copy()
    hover[2] += max(hover_clearance, 0.12)
    _, current_quat, _ = control.read_pose()
    quat = _canonical_topdown_push_quat(direction, current_quat)
    orientation_mode = "canonical_topdown"
    hover_recovery_reached = None
    hover_staging = {
        "hover_reached": False,
    }

    reached, _ = control.servo_to(
        hover,
        quat=quat,
        gripper="close",
        pos_tol=0.05,
        max_iters=180,
        via_trajopt=True,
    )
    measured, _, _ = control.read_pose()
    hover_within = bool(
        reached and float(np.linalg.norm(measured - hover)) <= 0.06
    )
    if not hover_within:
        hover_recovery_reached, _ = control.recover_ready_posture(
            max_iters=220
        )
        if hover_recovery_reached:
            reached, _ = control.servo_to(
                hover,
                quat=quat,
                gripper="close",
                pos_tol=0.05,
                max_iters=200,
                via_trajopt=True,
            )
            measured, _, _ = control.read_pose()
            hover_within = bool(
                reached and float(np.linalg.norm(measured - hover)) <= 0.06
            )
    hover_staging["hover_reached"] = hover_within
    if not hover_within:
        return _failure(
            env,
            "could not reach the vertical planar-push hover pose",
            orientation_mode=orientation_mode,
            hover_recovery_reached=bool(hover_recovery_reached),
            visual_contact_backoff=round(contact_backoff, 4),
            effective_standoff=round(effective_standoff, 4),
            hover_staging=hover_staging,
        )
    reached, _ = control.servo_to(
        contact,
        quat=quat,
        gripper="close",
        pos_tol=0.025,
        max_iters=100,
        via_trajopt=False,
    )
    measured, _, _ = control.read_pose()
    if not reached or float(np.linalg.norm(measured - contact)) > 0.035:
        return _failure(env, "could not reach the planar-push contact pose")

    push_start = np.asarray(measured, dtype=float)
    achieved = 0.0
    push_reached = True
    push_segments: list[dict[str, Any]] = []
    push_segments_completed = 0
    segment_count = max(
        1,
        int(np.ceil(push_distance / _MAX_PUSH_SEGMENT_DISTANCE)),
    )
    for segment_index in range(segment_count):
        requested_progress = min(
            push_distance,
            (segment_index + 1) * _MAX_PUSH_SEGMENT_DISTANCE,
        )
        segment_target = contact + xy_direction * requested_progress
        segment_reached, _ = control.servo_to(
            segment_target,
            quat=quat,
            gripper="close",
            pos_tol=0.025,
            max_iters=90,
        )
        measured, _, _ = control.read_pose()
        achieved = max(
            0.0,
            float(
                np.dot(
                    (np.asarray(measured, dtype=float) - push_start)[:2],
                    direction,
                )
            ),
        )
        target_error = float(
            np.linalg.norm(np.asarray(measured, dtype=float) - segment_target)
        )
        segment_within = bool(segment_reached and target_error <= 0.025)
        push_segments.append(
            {
                "index": segment_index,
                "requested_progress": round(requested_progress, 4),
                "measured_progress": round(achieved, 4),
                "target_error": round(target_error, 4),
                "reached": segment_within,
            }
        )
        if not segment_within:
            push_reached = False
            break
        push_segments_completed += 1
    retract_target = np.asarray(measured, dtype=float).copy()
    retract_target[2] += hover_clearance
    retracted, _ = control.servo_to(
        retract_target,
        quat=quat,
        gripper="close",
        pos_tol=0.05,
        max_iters=90,
    )
    control.set_gripper(close=False)
    pushed = bool(
        achieved >= min(_MAX_REQUIRED_PUSH_DISTANCE, push_distance * 0.75)
        and retracted
    )
    return (
        {
            "ok": pushed,
            "pushed": pushed,
            "reason": (
                "bounded planar push measured and arm retracted"
                if pushed
                else "planar push did not achieve enough measured motion"
            ),
            "push_reached": bool(push_reached),
            "retracted": bool(retracted),
            "requested_push_distance": round(push_distance, 4),
            "measured_push_distance": round(achieved, 4),
            "push_segments_attempted": len(push_segments),
            "push_segments_completed": push_segments_completed,
            "push_segments": push_segments,
            "visual_contact_backoff": round(contact_backoff, 4),
            "effective_standoff": round(effective_standoff, 4),
            "orientation_mode": orientation_mode,
            "hover_recovery_reached": hover_recovery_reached,
            "hover_staging": hover_staging,
            "source_pixel": list(source_pixel),
            "target_pixel": list(target_pixel),
            "source_point": [round(float(value), 4) for value in source],
            "target_point": [round(float(value), 4) for value in target],
        },
        env.take_snapshot(),
    )
