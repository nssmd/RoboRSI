"""Pure-vision vertical-hinge door opening for LIBERO."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)
from roborsi.embodied.skills.base._lib.libero.semantic_point import (
    matching_semantic_point,
)

_CONTACT_OFFSET = 0.012
_MIN_RADIUS = 0.06
_MAX_RADIUS = 0.45
_MIN_OPEN_ANGLE_DEG = 25.0
_ARC_STEP_DEG = 5.0


def _bounded(value: Any, default: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if not np.isfinite(result):
        result = default
    return float(np.clip(result, low, high))


def _pixel(value: Any, image: np.ndarray) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, (bool, np.bool_)) for item in value):
        return None
    try:
        coords = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        coords.shape != (2,)
        or not np.all(np.isfinite(coords))
        or not np.all(coords == np.floor(coords))
    ):
        return None
    u, v = int(coords[0]), int(coords[1])
    height, width = image.shape[:2]
    return (u, v) if 0 <= u < width and 0 <= v < height else None


def _point(
    env: Any,
    u: int,
    v: int,
    *,
    width: int,
    height: int,
) -> np.ndarray | None:
    if not (0 <= int(u) < int(width) and 0 <= int(v) < int(height)):
        return None
    value = env.pixel_to_world(int(u), int(v))
    try:
        point = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return None
    return point


def _infer_hinge_point(
    env: Any,
    *,
    handle_pixel: tuple[int, int],
    handle_point: np.ndarray,
    image_shape: tuple[int, ...],
    hinge_side: str,
) -> np.ndarray | None:
    height, width = image_shape[:2]
    direction = -1 if hinge_side == "left" else 1
    candidates: list[np.ndarray] = []
    for fraction in np.linspace(0.05, 0.42, 16):
        offset = max(4, int(round(float(width) * float(fraction))))
        for dv_fraction in (-0.015, 0.0, 0.015):
            dv = int(round(float(height) * dv_fraction))
            point = _point(
                env,
                int(handle_pixel[0]) + direction * offset,
                int(handle_pixel[1]) + dv,
                width=width,
                height=height,
            )
            if point is None:
                continue
            radius = float(
                np.linalg.norm(
                    (point - np.asarray(handle_point, dtype=float))[:2]
                )
            )
            if (
                _MIN_RADIUS <= radius <= _MAX_RADIUS
                and abs(float(point[2] - handle_point[2])) <= 0.10
            ):
                candidates.append(point)
    if not candidates:
        return None
    hinge = max(
        candidates,
        key=lambda point: float(
            np.linalg.norm((point - handle_point)[:2])
        ),
    ).copy()
    hinge[2] = float(handle_point[2])
    return hinge


def _door_face_normal(
    env: Any,
    u: int,
    v: int,
    handle: np.ndarray,
    image_shape: tuple[int, ...],
) -> np.ndarray | None:
    from roborsi.embodied.skills.base.pull_drawer.libero.policy import (
        _face_normal,
    )

    normal = _face_normal(env, u, v, handle, image_shape)
    if normal is not None:
        return np.asarray(normal, dtype=float)
    base = np.asarray(env.robot_base_pos(), dtype=float)
    fallback = base - np.asarray(handle, dtype=float)
    fallback[2] = 0.0
    norm = float(np.linalg.norm(fallback))
    return fallback / norm if norm > 1e-8 else None


def _drawer_handle_refinement(
    env: Any,
    u: int,
    v: int,
    image_shape: tuple[int, ...],
):
    from roborsi.embodied.skills.base.pull_drawer.libero.policy import (
        _refine_drawer_handle_geometry,
    )

    return _refine_drawer_handle_geometry(env, u, v, image_shape)


def _resolve_attached_handle(
    env: Any,
    *,
    pixel: tuple[int, int],
    raw_point: np.ndarray,
    image_shape: tuple[int, ...],
) -> tuple[tuple[int, int], np.ndarray, np.ndarray | None]:
    selected_uv = (int(pixel[0]), int(pixel[1]))
    selected_point = np.asarray(raw_point, dtype=float)
    refined = _drawer_handle_refinement(
        env,
        selected_uv[0],
        selected_uv[1],
        image_shape,
    )
    refined_normal = None
    if refined is not None:
        candidate_uv = (int(refined[0][0]), int(refined[0][1]))
        candidate_point = np.asarray(refined[1], dtype=float)
        pixel_jump = float(
            np.linalg.norm(
                np.asarray(candidate_uv, dtype=float)
                - np.asarray(selected_uv, dtype=float)
            )
        )
        xy_jump = float(
            np.linalg.norm((candidate_point - selected_point)[:2])
        )
        z_jump = abs(float(candidate_point[2] - selected_point[2]))
        if (
            candidate_point.shape == (3,)
            and np.all(np.isfinite(candidate_point))
            and pixel_jump <= 16.0
            and xy_jump <= 0.18
            and z_jump <= 0.12
        ):
            selected_uv = candidate_uv
            selected_point = candidate_point
            refined_normal = np.asarray(refined[2], dtype=float)

    toward_robot = np.asarray(env.robot_base_pos(), dtype=float) - selected_point
    toward_robot[2] = 0.0
    toward_norm = float(np.linalg.norm(toward_robot))
    if toward_norm <= 1e-8:
        return selected_uv, selected_point, None
    toward_robot /= toward_norm
    normal = refined_normal
    if normal is None:
        normal = _door_face_normal(
            env,
            selected_uv[0],
            selected_uv[1],
            selected_point,
            image_shape,
        )
    if normal is not None:
        normal = np.asarray(normal, dtype=float)
        normal[2] = 0.0
        normal_norm = float(np.linalg.norm(normal))
        normal = normal / normal_norm if normal_norm > 1e-8 else None
    if normal is None or float(np.dot(normal, toward_robot)) < 0.35:
        normal = toward_robot
    return selected_uv, selected_point, normal


def _rotate_xy(vector: np.ndarray, angle: float) -> np.ndarray:
    c, s = float(np.cos(angle)), float(np.sin(angle))
    value = np.asarray(vector, dtype=float)
    return np.array(
        [
            c * value[0] - s * value[1],
            s * value[0] + c * value[1],
            float(value[2]),
        ],
        dtype=float,
    )


def _measured_close(target: np.ndarray, measured: Any, tol: float) -> bool:
    try:
        point = np.asarray(measured, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        point.shape == (3,)
        and np.all(np.isfinite(point))
        and float(np.linalg.norm(point - target)) <= tol
    )


def _failure(env: Any, reason: str, **fields: Any):
    return (
        {"ok": False, "opened": False, "reason": reason, **fields},
        env.take_snapshot(),
    )


def dispatch_runtime(state: Any, args: dict[str, Any]):
    from roborsi.embodied.skills.base.pull_drawer.libero.policy import (
        _side_entry_quat,
    )

    env = state.env
    object_name = str(args.get("object") or "").strip()
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in object_name.split()
    }
    if "door" not in words or "handle" not in words:
        return _failure(env, "object must name an attached door handle")
    hinge_side = str(args.get("hinge_side") or "").strip().lower()
    if hinge_side not in {"left", "right"}:
        return _failure(env, "hinge_side must be left or right")
    snapshot = env.take_snapshot()
    image = (getattr(snapshot, "images", {}) or {}).get("head_camera")
    if image is None:
        return _failure(env, "head-camera image unavailable")
    image = np.asarray(image)
    uv = _pixel(args.get("pixel"), image)
    if uv is None:
        return _failure(env, "give a finite in-frame door-handle pixel=[u,v]")
    point_evidence = matching_semantic_point(
        env,
        object_name=object_name,
        pixel=uv,
        current_frame=image,
    )
    if point_evidence is None:
        return _failure(
            env,
            "current semantic door-handle point is missing or stale",
        )
    height, width = image.shape[:2]
    raw_handle = _point(
        env,
        uv[0],
        uv[1],
        width=width,
        height=height,
    )
    if raw_handle is None:
        return _failure(env, "door-handle depth is unavailable")
    uv, handle, normal = _resolve_attached_handle(
        env,
        pixel=uv,
        raw_point=raw_handle,
        image_shape=image.shape,
    )
    hinge = _infer_hinge_point(
        env,
        handle_pixel=uv,
        handle_point=handle,
        image_shape=image.shape,
        hinge_side=hinge_side,
    )
    if hinge is None:
        return _failure(env, "could not infer a same-height visual hinge edge")
    radial = np.asarray(handle, dtype=float) - np.asarray(hinge, dtype=float)
    radial[2] = 0.0
    radius = float(np.linalg.norm(radial))
    if not _MIN_RADIUS <= radius <= _MAX_RADIUS:
        return _failure(env, "inferred hinge radius is outside safe bounds")
    radial /= radius
    if normal is None:
        return _failure(env, "could not infer the outward door-face normal")
    normal = np.asarray(normal, dtype=float)
    normal[2] = 0.0
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-8:
        return _failure(env, "door-face normal is degenerate")
    normal /= normal_norm

    ctrl = LiberoControl(env)
    gap_initial, gripper_initial = ctrl.read_gripper_state()
    if gripper_initial is GripperState.HELD:
        return _failure(
            env,
            "refusing door motion while holding another object",
            gripper_gap=round(float(gap_initial), 4),
        )
    if gripper_initial is not GripperState.OPEN:
        ctrl.set_gripper(close=False)
        _, gripper_initial = ctrl.read_gripper_state()
        if gripper_initial is not GripperState.OPEN:
            return _failure(env, "could not open gripper before approach")

    approach_distance = _bounded(args.get("approach"), 0.09, 0.05, 0.14)
    requested_angle = _bounded(args.get("angle_deg"), 65.0, 25.0, 95.0)
    approach = handle + normal * approach_distance
    contact = handle - normal * _CONTACT_OFFSET
    quat = _side_entry_quat(normal)
    reached, _ = ctrl.servo_to(
        approach,
        quat=quat,
        gripper="open",
        pos_tol=0.04,
        max_iters=120,
        via_trajopt=True,
    )
    measured, _, _ = ctrl.read_pose()
    if not reached or not _measured_close(approach, measured, 0.05):
        return _failure(env, "could not reach the door-handle approach pose")
    reached, _ = ctrl.servo_to(
        contact,
        quat=quat,
        gripper="open",
        pos_tol=0.025,
        max_iters=100,
    )
    measured, _, _ = ctrl.read_pose()
    if not reached or not _measured_close(contact, measured, 0.04):
        return _failure(env, "could not reach the door handle")
    ctrl.set_gripper(close=True)
    gap_closed, gripper_closed = ctrl.read_gripper_state()
    if gripper_closed is not GripperState.HELD:
        ctrl.set_gripper(close=False)
        return _failure(
            env,
            "gripper closed without securing the attached handle",
            gripper_gap=round(float(gap_closed), 4),
        )

    tangent_ccw = np.array([-radial[1], radial[0], 0.0], dtype=float)
    sign = 1.0 if float(np.dot(tangent_ccw, normal)) >= 0.0 else -1.0
    arc_steps = max(5, int(math.ceil(requested_angle / _ARC_STEP_DEG)))
    achieved_angle = 0.0
    final_normal = normal.copy()
    for angle_deg in np.linspace(0.0, requested_angle, arc_steps + 1)[1:]:
        angle = math.radians(float(angle_deg)) * sign
        rotated_radial = _rotate_xy(radial, angle)
        final_normal = _rotate_xy(normal, angle)
        target_handle = np.asarray(hinge, dtype=float) + radius * rotated_radial
        target_handle[2] = float(handle[2])
        target = target_handle - final_normal * _CONTACT_OFFSET
        reached, _ = ctrl.servo_to(
            target,
            quat=_side_entry_quat(final_normal),
            gripper="close",
            pos_tol=0.02,
            max_iters=45,
        )
        measured, _, _ = ctrl.read_pose()
        _, hold_state = ctrl.read_gripper_state()
        if (
            not reached
            or not _measured_close(target, measured, 0.04)
            or hold_state is not GripperState.HELD
        ):
            break
        achieved_angle = float(angle_deg)

    ctrl.set_gripper(close=False)
    gap_after, gripper_after = ctrl.read_gripper_state()
    measured, _, _ = ctrl.read_pose()
    retract_target = (
        np.asarray(measured, dtype=float)
        + final_normal * 0.08
        + np.array([0.0, 0.0, 0.06], dtype=float)
    )
    retracted, _ = ctrl.servo_to(
        retract_target,
        quat=_side_entry_quat(final_normal),
        gripper="open",
        pos_tol=0.05,
        max_iters=90,
    )
    opened = bool(
        achieved_angle >= _MIN_OPEN_ANGLE_DEG
        and gripper_after is GripperState.OPEN
        and retracted
    )
    return (
        {
            "ok": opened,
            "opened": opened,
            "reason": (
                "measured hinged-door arc completed, released, and retracted"
                if opened
                else "door arc motion was insufficient"
            ),
            "achieved_angle_deg": round(achieved_angle, 2),
            "requested_angle_deg": round(requested_angle, 2),
            "hinge_radius": round(radius, 4),
            "hinge_point": [round(float(value), 4) for value in hinge],
            "handle_point": [round(float(value), 4) for value in handle],
            "handle_pixel": [int(uv[0]), int(uv[1])],
            "arc_steps": arc_steps,
            "retracted": bool(retracted),
            "gripper_state_after": gripper_after.value,
            "gripper_gap_after": round(float(gap_after), 4),
        },
        env.take_snapshot(),
    )
