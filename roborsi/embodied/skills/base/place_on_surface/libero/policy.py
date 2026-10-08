"""Pure-vision placement onto an exposed LIBERO support surface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import (
    LiberoControl,
    bounded_residual_correction_target,
)
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    get_visual_hold,
    verify_visual_hold,
)

_PIXEL_CLOUD_MAX_XY_ERROR = 0.12
_MIN_SURFACE_POINTS = 20
_STAGED_DESCENT_HEIGHT = 0.02
_MAX_RELEASE_Z_ERROR = 0.008
_MAX_APPROACH_RESIDUAL_XY = 0.03
_MAX_APPROACH_CORRECTION_MOVE = 0.03
_APPROACH_CORRECTION_ROT_TOL = 0.08
_SURFACE_TARGET_CACHE_ATTR = "_roborsi_surface_target_cache"


@dataclass(frozen=True)
class SurfaceTarget:
    pixel: tuple[int, int]
    world: np.ndarray
    source: str
    frame_token: str | None = None
    sensor_frame: Any | None = None
    support_mask: np.ndarray | None = None
    robot_mask: np.ndarray | None = None


def _surface_target_key(
    target_name: str,
    pixel: tuple[int, int],
) -> tuple[str, tuple[int, int]]:
    tokens = re.findall(
        r"[a-z0-9]+",
        str(target_name or "").lower().replace("_", " "),
    )
    normalized = " ".join(
        token for token in tokens if token not in {"a", "an", "the"}
    )
    return normalized, pixel


def _cached_surface_target(
    env: Any,
    key: tuple[str, tuple[int, int]],
) -> SurfaceTarget | None:
    cache = getattr(env, _SURFACE_TARGET_CACHE_ATTR, None)
    if not isinstance(cache, dict):
        return None
    target = cache.get(key)
    if not isinstance(target, SurfaceTarget):
        return None
    return SurfaceTarget(
        pixel=target.pixel,
        world=np.asarray(target.world, dtype=float).copy(),
        source=f"cached:{target.source}",
        frame_token=target.frame_token,
        sensor_frame=target.sensor_frame,
        support_mask=(
            None if target.support_mask is None else target.support_mask.copy()
        ),
        robot_mask=(
            None if target.robot_mask is None else target.robot_mask.copy()
        ),
    )


def _cache_surface_target(
    env: Any,
    key: tuple[str, tuple[int, int]],
    target: SurfaceTarget,
) -> SurfaceTarget:
    cache = getattr(env, _SURFACE_TARGET_CACHE_ATTR, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(env, _SURFACE_TARGET_CACHE_ATTR, cache)
    cache[key] = SurfaceTarget(
        pixel=target.pixel,
        world=np.asarray(target.world, dtype=float).copy(),
        source=target.source,
        frame_token=target.frame_token,
        sensor_frame=target.sensor_frame,
        support_mask=(
            None if target.support_mask is None else target.support_mask.copy()
        ),
        robot_mask=(
            None if target.robot_mask is None else target.robot_mask.copy()
        ),
    )
    return target


def _clear_surface_target_cache(env: Any) -> None:
    if hasattr(env, _SURFACE_TARGET_CACHE_ATTR):
        delattr(env, _SURFACE_TARGET_CACHE_ATTR)


def _valid_world_point(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    point = np.asarray(value, dtype=float)
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return None
    return point


def _finite_cloud(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    cloud = np.asarray(value, dtype=float)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return None
    finite = cloud[np.all(np.isfinite(cloud), axis=1)]
    return finite if len(finite) else None


def _head_image_size(env: Any) -> tuple[int, int] | None:
    image = env.take_snapshot().images.get("head_camera")
    if image is None:
        return None
    array = np.asarray(image)
    if array.ndim < 2:
        return None
    height, width = array.shape[:2]
    if height <= 0 or width <= 0:
        return None
    return int(width), int(height)


def _resolve_surface_target(
    state: Any,
    ctrl: LiberoControl,
    *,
    target_name: str,
    pixel: Any,
    held_quat: np.ndarray | None = None,
) -> SurfaceTarget | None:
    env = state.env
    image_size = _head_image_size(env)
    if image_size is None:
        return None

    if isinstance(pixel, (list, tuple)) and len(pixel) == 2:
        try:
            uv = int(pixel[0]), int(pixel[1])
        except (TypeError, ValueError, OverflowError):
            return None
    elif target_name:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            localize_precise,
        )

        # Do not call retreat_from_head_view here: that helper intentionally moves
        # the held object out of agentview, while placement authentication requires
        # the pickup-referenced instance to remain visible. Authenticate the hold
        # on the current coherent frame, then localize without stepping. Repeated
        # coherent captures do not advance the observation generation, and the
        # exact target frame is associated again below before any motion or release.
        _, current_hold_state = ctrl.read_gripper_state()
        if current_hold_state is not GripperState.HELD:
            return None
        try:
            localization_frame = env.coherent_sensor_frame("agentview")
        except Exception:  # noqa: BLE001
            return None
        if not localization_frame.valid:
            return None
        localization_hold = verify_visual_hold(
            env,
            localization_frame.rgb,
            holding=True,
            frame=localization_frame,
        )
        if not localization_hold.ok:
            return None
        located = localize_precise(state, target_name)
        if located is None:
            return None
        try:
            uv = int(located[0]), int(located[1])
        except (TypeError, ValueError, OverflowError, IndexError):
            return None
        if abs(uv[0] - 128) <= 2 and abs(uv[1] - 128) <= 2:
            return None
    else:
        return None

    width, height = image_size
    if not (0 <= uv[0] < width and 0 <= uv[1] < height):
        return None
    try:
        frame = env.coherent_sensor_frame("agentview")
    except Exception:  # noqa: BLE001
        return None
    if not frame.valid:
        return None
    from roborsi.embodied.skills.base._lib.libero._perception import (
        sam_mask_at_point,
    )
    from roborsi.embodied.skills.base._lib.libero.instance_hold import (
        mask_world_cloud,
        robot_projection_for_frame,
    )

    try:
        projection = robot_projection_for_frame(env, frame)
        mask = np.asarray(
            sam_mask_at_point(frame.rgb, uv[0], uv[1]),
            dtype=bool,
        )
    except Exception:  # noqa: BLE001
        return None
    if mask.shape != frame.depth_m.shape or not mask[uv[1], uv[0]]:
        return None
    robot_mask = np.asarray(projection.robot_mask, dtype=bool)
    if robot_mask.shape != mask.shape or robot_mask[uv[1], uv[0]]:
        return None
    mask = mask & ~robot_mask
    area = int(mask.sum())
    fraction = area / float(mask.size)
    if area < _MIN_SURFACE_POINTS or fraction > 0.45:
        return None
    cloud = mask_world_cloud(frame, mask)
    if cloud is None or len(cloud) < _MIN_SURFACE_POINTS:
        return None
    world = np.array(
        [
            float(np.median(cloud[:, 0])),
            float(np.median(cloud[:, 1])),
            float(np.percentile(cloud[:, 2], 85)),
        ],
        dtype=float,
    )
    mask = mask.copy()
    robot_mask = robot_mask.copy()
    mask.setflags(write=False)
    robot_mask.setflags(write=False)
    return SurfaceTarget(
        pixel=uv,
        world=world,
        source="phrase_pixel+point_sam+coherent_rgbd",
        frame_token=frame.frame_token,
        sensor_frame=frame,
        support_mask=mask,
        robot_mask=robot_mask,
    )


def _bounded_arg(
    args: dict[str, Any],
    name: str,
    default: float,
    low: float,
    high: float | None = None,
) -> float:
    try:
        value = float(args.get(name, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    if not np.isfinite(value):
        value = default
    value = max(low, value)
    return value if high is None else min(high, value)


def _target_fields(target: SurfaceTarget) -> dict[str, Any]:
    return {
        "target_pixel": list(target.pixel),
        "target_world": [round(float(v), 4) for v in target.world],
        "target_source": target.source,
    }


def _rounded_metric(value: float) -> float | None:
    return round(float(value), 4) if np.isfinite(value) else None


def _save_hold_diagnostic(
    state: Any,
    verification: Any,
    frame: Any | None,
    *,
    stage: str,
) -> str | None:
    """Persist only public coherent RGB and derived masks for trial diagnosis."""
    if frame is None or not getattr(frame, "valid", False):
        return None
    candidate = getattr(verification, "instance_mask", None)
    robot = getattr(verification, "robot_mask", None)
    if candidate is None and robot is None:
        return None
    try:
        directory = Path(
            getattr(state, "workdir", "/tmp/roborsi-place-diagnostics")
        ) / "place_on_surface_diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        token = re.sub(
            r"[^a-zA-Z0-9_.-]+",
            "_",
            str(getattr(frame, "frame_token", "unknown")),
        )
        path = directory / f"{stage}-{token}.npz"
        np.savez_compressed(
            path,
            rgb=np.asarray(frame.rgb).copy(),
            candidate_mask=(
                np.asarray(candidate, dtype=bool).copy()
                if candidate is not None
                else np.empty((0, 0), dtype=bool)
            ),
            robot_mask=(
                np.asarray(robot, dtype=bool).copy()
                if robot is not None
                else np.empty((0, 0), dtype=bool)
            ),
        )
        return str(path)
    except (OSError, TypeError, ValueError):
        return None


def _motion_error_fields(
    prefix: str,
    target: np.ndarray,
    ee_pos: Any,
    ee_quat: Any,
    target_quat: np.ndarray,
) -> dict[str, Any]:
    measured = _valid_world_point(ee_pos)
    quat = np.asarray(ee_quat, dtype=float)
    position_error = (
        float(np.linalg.norm(measured - target))
        if measured is not None
        else float("inf")
    )
    z_error = (
        abs(float(measured[2] - target[2]))
        if measured is not None
        else float("inf")
    )
    rotation_error = float("inf")
    if quat.shape == (4,) and np.all(np.isfinite(quat)):
        qn = float(np.linalg.norm(quat))
        tn = float(np.linalg.norm(target_quat))
        if qn > 0.0 and tn > 0.0:
            dot = float(
                np.clip(
                    abs(np.dot(quat / qn, target_quat / tn)),
                    0.0,
                    1.0,
                )
            )
            rotation_error = 2.0 * float(np.arccos(dot))
    return {
        f"{prefix}_target": [round(float(v), 4) for v in target],
        f"{prefix}_position_error": _rounded_metric(position_error),
        f"{prefix}_z_error": _rounded_metric(z_error),
        f"{prefix}_rotation_error_rad": _rounded_metric(rotation_error),
        "ee_pos": (
            [round(float(v), 4) for v in measured]
            if measured is not None
            else None
        ),
    }


def _approach_correction_target(
    target: np.ndarray,
    ee_pos: Any,
    ee_quat: Any,
    target_quat: np.ndarray,
    *,
    gate_pos_tol: float,
) -> np.ndarray | None:
    measured = _valid_world_point(ee_pos)
    quat = np.asarray(ee_quat, dtype=float)
    if measured is None or quat.shape != (4,) or not np.all(np.isfinite(quat)):
        return None
    qn = float(np.linalg.norm(quat))
    tn = float(np.linalg.norm(target_quat))
    if qn <= 0.0 or tn <= 0.0:
        return None
    dot = float(
        np.clip(
            abs(np.dot(quat / qn, target_quat / tn)),
            0.0,
            1.0,
        )
    )
    rotation_error = 2.0 * float(np.arccos(dot))
    residual = np.asarray(target, dtype=float) - measured
    total_error = float(np.linalg.norm(residual))
    xy_error = float(np.linalg.norm(residual[:2]))
    z_error = abs(float(residual[2]))
    if (
        total_error <= gate_pos_tol
        or xy_error > _MAX_APPROACH_RESIDUAL_XY
        or z_error > gate_pos_tol
        or rotation_error > _APPROACH_CORRECTION_ROT_TOL
    ):
        return None
    corrected = measured.copy()
    desired_move = 2.0 * residual[:2]
    move_norm = float(np.linalg.norm(desired_move))
    xy_budget = float(
        np.sqrt(
            max(
                0.0,
                _MAX_APPROACH_CORRECTION_MOVE ** 2
                - residual[2] ** 2,
            )
        )
    )
    if move_norm > xy_budget:
        desired_move *= xy_budget / move_norm
    corrected[:2] = measured[:2] + desired_move
    corrected[2] = target[2]
    return corrected


def _measured_pose_within(
    target: np.ndarray,
    ee_pos: Any,
    ee_quat: Any,
    target_quat: np.ndarray,
    *,
    pos_tol: float,
    z_tol: float,
    rot_tol: float,
) -> bool:
    measured = _valid_world_point(ee_pos)
    quat = np.asarray(ee_quat, dtype=float)
    if measured is None or quat.shape != (4,) or not np.all(np.isfinite(quat)):
        return False
    qn = float(np.linalg.norm(quat))
    tn = float(np.linalg.norm(target_quat))
    if qn <= 0.0 or tn <= 0.0:
        return False
    dot = float(
        np.clip(
            abs(np.dot(quat / qn, target_quat / tn)),
            0.0,
            1.0,
        )
    )
    return bool(
        float(np.linalg.norm(measured - target)) <= pos_tol
        and abs(float(measured[2] - target[2])) <= z_tol
        and 2.0 * float(np.arccos(dot)) <= rot_tol
    )


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    ctrl = LiberoControl(env)
    gap_before, state_before = ctrl.read_gripper_state()
    if state_before is not GripperState.HELD:
        _clear_surface_target_cache(env)
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": (
                    f"gripper state is {state_before.value}, "
                    "not a confirmed hold"
                ),
                "gripper_state_before": state_before.value,
                "gripper_gap_before": round(float(gap_before), 4),
            },
            env.take_snapshot(),
        )

    try:
        initial_hold_frame = env.coherent_sensor_frame("agentview")
    except Exception:  # noqa: BLE001
        initial_hold_frame = None
    initial_visual_hold = verify_visual_hold(
        env,
        (
            initial_hold_frame.rgb
            if initial_hold_frame is not None and initial_hold_frame.valid
            else env.take_snapshot().images.get("head_camera")
        ),
        holding=True,
        frame=initial_hold_frame,
    )
    if not initial_visual_hold.ok:
        diagnostic_path = _save_hold_diagnostic(
            state,
            initial_visual_hold,
            initial_hold_frame,
            stage="initial_hold",
        )
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": (
                    "visual hold evidence is invalid; "
                    "no target localization or motion was attempted"
                ),
                "source_clear_verified": False,
                "source_clear_reason": initial_visual_hold.reason,
                "evidence_object": initial_visual_hold.object_name,
                "association_frame_token": initial_visual_hold.frame_token,
                "association_metrics": initial_visual_hold.association_metrics,
                "public_sensor_diagnostic": diagnostic_path,
                "visual_source_mad": (
                    round(float(initial_visual_hold.current_source_mad), 4)
                    if initial_visual_hold.current_source_mad is not None
                    else None
                ),
                "visual_source_to_after_mad": (
                    round(
                        float(initial_visual_hold.current_to_after_mad),
                        4,
                    )
                    if initial_visual_hold.current_to_after_mad is not None
                    else None
                ),
                "gripper_state_before": state_before.value,
                "gripper_gap_before": round(float(gap_before), 4),
            },
            env.take_snapshot(),
        )

    _, held_quat, _ = ctrl.read_pose()
    held_quat = np.asarray(held_quat, dtype=float)
    if held_quat.shape != (4,) or not np.all(np.isfinite(held_quat)):
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "current end-effector orientation is invalid",
                "gripper_state_before": state_before.value,
            },
            env.take_snapshot(),
        )

    target = _resolve_surface_target(
        state,
        ctrl,
        target_name=str(args.get("target") or "").strip(),
        pixel=args.get("pixel"),
        held_quat=held_quat,
    )
    if target is None:
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "could not resolve a valid exposed target surface",
                "gripper_state_before": state_before.value,
                "gripper_gap_before": round(float(gap_before), 4),
            },
            env.take_snapshot(),
        )

    # Reassociate using the exact coherent RGB-D frame consumed by support
    # segmentation. Do not independently recapture and infer synchronization
    # from token equality after the fact.
    target_visual_hold = verify_visual_hold(
        env,
        (
            target.sensor_frame.rgb
            if target.sensor_frame is not None
            else None
        ),
        holding=True,
        frame=target.sensor_frame,
    )
    alias_reason = None
    if (
        target.sensor_frame is None
        or not target_visual_hold.ok
        or target_visual_hold.frame_token != target.frame_token
        or target_visual_hold.instance_mask is None
        or target.support_mask is None
        or target_visual_hold.robot_mask is None
    ):
        alias_reason = "held/support evidence is not synchronized"
    else:
        import cv2

        held_mask = np.asarray(target_visual_hold.instance_mask, dtype=bool)
        support_mask = np.asarray(target.support_mask, dtype=bool)
        robot_mask = np.asarray(target_visual_hold.robot_mask, dtype=bool)
        if (
            held_mask.shape != support_mask.shape
            or held_mask.shape != robot_mask.shape
        ):
            alias_reason = "held/support mask shape mismatch"
        elif held_mask[target.pixel[1], target.pixel[0]]:
            alias_reason = "target pixel lies on the authenticated held object"
        elif robot_mask[target.pixel[1], target.pixel[0]]:
            alias_reason = "target pixel lies on rendered robot geometry"
        else:
            intersection = int(np.logical_and(held_mask, support_mask).sum())
            denominator = max(1, min(int(held_mask.sum()), int(support_mask.sum())))
            expanded_held = cv2.dilate(
                held_mask.astype(np.uint8),
                np.ones((7, 7), dtype=np.uint8),
            ).astype(bool)
            if intersection / float(denominator) > 0.05:
                alias_reason = "held object and support masks alias"
            elif np.logical_and(expanded_held, support_mask).any():
                alias_reason = "held object and support are not separated under uncertainty"
    if alias_reason is not None:
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": alias_reason,
                "source_clear_verified": target_visual_hold.ok,
                "source_clear_reason": target_visual_hold.reason,
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    release_clearance = _bounded_arg(
        args,
        "release_clearance",
        0.025,
        0.01,
        0.05,
    )
    hover = _bounded_arg(args, "hover", 0.12, 0.05)
    pos_tol = _bounded_arg(args, "pos_tol", 0.02, 0.005, 0.05)
    approach_pos_tol = min(pos_tol, 0.015)
    release_pos_tol = min(pos_tol, _MAX_RELEASE_Z_ERROR)

    held_object_offset_xy = None
    held_object_bottom_offset_z = None
    evidence = get_visual_hold(env)
    if evidence is not None and evidence.object_offset_local is not None:
        candidate_offset = np.asarray(
            evidence.object_offset_local,
            dtype=float,
        )
        if (
            candidate_offset.shape == (3,)
            and np.all(np.isfinite(candidate_offset))
            and float(np.linalg.norm(candidate_offset)) <= 0.10
        ):
            from scipy.spatial.transform import Rotation

            held_rotation = Rotation.from_quat(held_quat)
            world_offset = held_rotation.apply(candidate_offset)
            if (
                np.all(np.isfinite(world_offset))
                and float(np.linalg.norm(world_offset[:2])) <= 0.08
            ):
                held_object_offset_xy = world_offset[:2]
            reference = evidence.pickup_reference
            local_cloud = np.asarray(
                getattr(reference, "pickup_cloud_local", []),
                dtype=float,
            )
            if (
                local_cloud.ndim == 2
                and local_cloud.shape[1] == 3
                and len(local_cloud) >= _MIN_SURFACE_POINTS
                and np.all(np.isfinite(local_cloud))
            ):
                rotated_cloud = held_rotation.apply(local_cloud)
                bottom_offset = float(np.percentile(rotated_cloud[:, 2], 5))
                vertical_span = float(
                    np.percentile(rotated_cloud[:, 2], 95) - bottom_offset
                )
                if (
                    np.isfinite(bottom_offset)
                    and np.isfinite(vertical_span)
                    and 0.003 <= vertical_span <= 0.20
                    and -0.20 <= bottom_offset <= 0.10
                ):
                    held_object_bottom_offset_z = bottom_offset
    release_world = np.asarray(target.world, dtype=float).copy()
    if held_object_offset_xy is not None:
        release_world[:2] -= held_object_offset_xy
    # release_clearance is clearance under the authenticated held object, not
    # under the TCP. A bowl commonly extends well below the TCP and otherwise
    # contacts the support before the commanded TCP pose can converge.
    bottom_offset_z = (
        held_object_bottom_offset_z
        if held_object_bottom_offset_z is not None
        else 0.0
    )
    release_pose = release_world + np.array(
        [0.0, 0.0, release_clearance - bottom_offset_z],
        dtype=float,
    )
    hover_pose = release_pose + np.array([0.0, 0.0, hover], dtype=float)

    approach_reached, _ = ctrl.servo_to(
        hover_pose,
        quat=held_quat,
        gripper="close",
        pos_tol=approach_pos_tol,
        max_iters=120,
        via_trajopt=True,
    )
    approach_correction_attempted = False
    approach_correction_blocked_reason = None
    if not approach_reached:
        ee_after_approach, quat_after_approach, _ = ctrl.read_pose()
        if _measured_pose_within(
            hover_pose,
            ee_after_approach,
            quat_after_approach,
            held_quat,
            pos_tol=approach_pos_tol,
            z_tol=approach_pos_tol,
            rot_tol=_APPROACH_CORRECTION_ROT_TOL,
        ):
            approach_reached = True
        else:
            correction_target = _approach_correction_target(
                hover_pose,
                ee_after_approach,
                quat_after_approach,
                held_quat,
                gate_pos_tol=approach_pos_tol,
            )
            if correction_target is not None:
                _, state_before_correction = (
                    ctrl.read_gripper_state()
                )
                visual_before_correction = verify_visual_hold(
                    env,
                    env.take_snapshot().images.get("head_camera"),
                    holding=(
                        state_before_correction is GripperState.HELD
                    ),
                )
                if (
                    state_before_correction is GripperState.HELD
                    and visual_before_correction.ok
                ):
                    approach_correction_attempted = True
                    ctrl.servo_to(
                        correction_target,
                        quat=held_quat,
                        gripper="close",
                        pos_tol=approach_pos_tol,
                        rot_tol=_APPROACH_CORRECTION_ROT_TOL,
                        max_iters=80,
                    )
                    ee_after_approach, quat_after_approach, _ = (
                        ctrl.read_pose()
                    )
                    approach_reached = _measured_pose_within(
                        hover_pose,
                        ee_after_approach,
                        quat_after_approach,
                        held_quat,
                        pos_tol=approach_pos_tol,
                        z_tol=approach_pos_tol,
                        rot_tol=_APPROACH_CORRECTION_ROT_TOL,
                    )
                elif state_before_correction is not GripperState.HELD:
                    approach_correction_blocked_reason = (
                        "hold_lost_before_correction"
                    )
                else:
                    approach_correction_blocked_reason = (
                        "visual_hold_failed_before_correction"
                    )
    if not approach_reached:
        ee_after_approach, quat_after_approach, _ = ctrl.read_pose()
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "could not reach the surface hover pose",
                "approach_correction_attempted": (
                    approach_correction_attempted
                ),
                "approach_correction_blocked_reason": (
                    approach_correction_blocked_reason
                ),
                "gripper_state_before": state_before.value,
                **_motion_error_fields(
                    "approach",
                    hover_pose,
                    ee_after_approach,
                    quat_after_approach,
                    held_quat,
                ),
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    gap_after_approach, state_after_approach = ctrl.read_gripper_state()
    visual_after_approach = verify_visual_hold(
        env,
        env.take_snapshot().images.get("head_camera"),
        holding=state_after_approach is GripperState.HELD,
    )
    if (
        state_after_approach is not GripperState.HELD
        or not visual_after_approach.ok
    ):
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "hold check failed after surface approach",
                "hold_check_stage": "approach",
                "gripper_state_at_gate": state_after_approach.value,
                "gripper_gap_at_gate": round(
                    float(gap_after_approach),
                    4,
                ),
                "source_clear_verified": visual_after_approach.ok,
                "source_clear_reason": visual_after_approach.reason,
                "evidence_object": visual_after_approach.object_name,
                "visual_source_mad": (
                    round(
                        float(visual_after_approach.current_source_mad),
                        4,
                    )
                    if visual_after_approach.current_source_mad is not None
                    else None
                ),
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    staged_pose = release_pose + np.array(
        [0.0, 0.0, _STAGED_DESCENT_HEIGHT],
        dtype=float,
    )
    stage_reached, _ = ctrl.servo_to(
        staged_pose,
        quat=held_quat,
        gripper="close",
        pos_tol=pos_tol,
        rot_tol=0.08,
        max_iters=80,
    )
    if not stage_reached:
        ee_after_stage, quat_after_stage, _ = ctrl.read_pose()
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "could not reach the staged surface descent pose",
                "gripper_state_before": state_before.value,
                **_motion_error_fields(
                    "stage",
                    staged_pose,
                    ee_after_stage,
                    quat_after_stage,
                    held_quat,
                ),
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    ee_at_stage, _, _ = ctrl.read_pose()
    ee_at_stage = _valid_world_point(ee_at_stage)
    stage_position_error = (
        float(np.linalg.norm(ee_at_stage - staged_pose))
        if ee_at_stage is not None
        else float("inf")
    )
    stage_z_error = (
        abs(float(ee_at_stage[2] - staged_pose[2]))
        if ee_at_stage is not None
        else float("inf")
    )
    if (
        stage_position_error > pos_tol
        or stage_z_error > pos_tol
    ):
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "staged surface descent pose was not measured",
                "stage_position_error": _rounded_metric(
                    stage_position_error
                ),
                "stage_z_error": _rounded_metric(stage_z_error),
                "ee_pos": (
                    [round(float(v), 4) for v in ee_at_stage]
                    if ee_at_stage is not None
                    else None
                ),
                "gripper_state_before": state_before.value,
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    gap_after_stage, state_after_stage = ctrl.read_gripper_state()
    visual_after_stage = verify_visual_hold(
        env,
        env.take_snapshot().images.get("head_camera"),
        holding=state_after_stage is GripperState.HELD,
    )
    if state_after_stage is not GripperState.HELD or not visual_after_stage.ok:
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": "hold check failed after staged surface descent",
                "hold_check_stage": "staged_descent",
                "gripper_state_at_gate": state_after_stage.value,
                "gripper_gap_at_gate": round(float(gap_after_stage), 4),
                "source_clear_verified": visual_after_stage.ok,
                "source_clear_reason": visual_after_stage.reason,
                "evidence_object": visual_after_stage.object_name,
                "visual_source_mad": (
                    round(float(visual_after_stage.current_source_mad), 4)
                    if visual_after_stage.current_source_mad is not None
                    else None
                ),
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    descent_reached, _ = ctrl.servo_to(
        release_pose,
        quat=held_quat,
        gripper="close",
        pos_tol=pos_tol,
        rot_tol=0.08,
        max_iters=150,
    )
    ee_before_release, quat_before_release, _ = ctrl.read_pose()
    ee_before_release = _valid_world_point(ee_before_release)
    pre_release_error = (
        float(np.linalg.norm(ee_before_release - release_pose))
        if ee_before_release is not None
        else float("inf")
    )
    pre_release_z_error = (
        abs(float(ee_before_release[2] - release_pose[2]))
        if ee_before_release is not None
        else float("inf")
    )
    reached = bool(
        descent_reached
        and pre_release_error <= pos_tol
        and pre_release_z_error <= release_pos_tol
    )
    final_correction_attempted = False
    final_correction_blocked_reason = None
    if (
        not reached
        and ee_before_release is not None
        and pre_release_error <= pos_tol
        and pre_release_z_error <= pos_tol
        and float(ee_before_release[2]) >= float(release_pose[2])
    ):
        _, state_before_correction = ctrl.read_gripper_state()
        visual_before_correction = verify_visual_hold(
            env,
            env.take_snapshot().images.get("head_camera"),
            holding=state_before_correction is GripperState.HELD,
        )
        if (
            state_before_correction is GripperState.HELD
            and visual_before_correction.ok
        ):
            correction_target = bounded_residual_correction_target(
                release_pose,
                ee_before_release,
                max_total_error=pos_tol,
                max_xy_error=pos_tol,
                max_z_error=pos_tol,
                max_move=pos_tol,
            )
            if correction_target is not None:
                final_correction_attempted = True
                ctrl.servo_correction_to(
                    correction_target,
                    quat=held_quat,
                    gripper="close",
                    pos_tol=release_pos_tol,
                    rot_tol=0.08,
                    max_iters=80,
                )
                ee_before_release, quat_before_release, _ = ctrl.read_pose()
                ee_before_release = _valid_world_point(ee_before_release)
                pre_release_error = (
                    float(
                        np.linalg.norm(
                            ee_before_release - release_pose
                        )
                    )
                    if ee_before_release is not None
                    else float("inf")
                )
                pre_release_z_error = (
                    abs(
                        float(
                            ee_before_release[2] - release_pose[2]
                        )
                    )
                    if ee_before_release is not None
                    else float("inf")
                )
                reached = bool(
                    pre_release_error <= pos_tol
                    and pre_release_z_error <= release_pos_tol
                )
            else:
                final_correction_blocked_reason = (
                    "final_residual_not_correctable"
                )
        elif state_before_correction is not GripperState.HELD:
            final_correction_blocked_reason = (
                "hold_lost_before_final_correction"
            )
        else:
            final_correction_blocked_reason = (
                "visual_hold_failed_before_final_correction"
            )
    if not reached:
        return (
            {
                "ok": False,
                "reached": False,
                "released": False,
                "reason": (
                    "surface release pose was not reached; "
                    "gripper kept closed"
                ),
                "pre_release_error": _rounded_metric(pre_release_error),
                "pre_release_z_error": _rounded_metric(
                    pre_release_z_error
                ),
                "final_correction_attempted": (
                    final_correction_attempted
                ),
                "final_correction_blocked_reason": (
                    final_correction_blocked_reason
                ),
                "gripper_state_before": state_before.value,
                "ee_pos": (
                    [round(float(v), 4) for v in ee_before_release]
                    if ee_before_release is not None
                    else None
                ),
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    gap_pre_release, state_pre_release = ctrl.read_gripper_state()
    if state_pre_release is not GripperState.HELD:
        return (
            {
                "ok": False,
                "reached": True,
                "released": False,
                "reason": (
                    "hold was lost before release; "
                    "no open command was issued"
                ),
                "pre_release_error": round(pre_release_error, 4),
                "pre_release_z_error": round(pre_release_z_error, 4),
                "source_clear_verified": False,
                "gripper_hold_continuity": False,
                "gripper_state_before": state_before.value,
                "gripper_state_pre_release": state_pre_release.value,
                "gripper_gap_pre_release": round(
                    float(gap_pre_release),
                    4,
                ),
                "ee_pos": [round(float(v), 4) for v in ee_before_release],
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    visual_hold = verify_visual_hold(
        env,
        env.take_snapshot().images.get("head_camera"),
        holding=True,
    )
    if not visual_hold.ok:
        return (
            {
                "ok": False,
                "reached": True,
                "released": False,
                "reason": (
                    "visual hold evidence failed before release; "
                    "no open command was issued"
                ),
                "pre_release_error": round(pre_release_error, 4),
                "pre_release_z_error": round(pre_release_z_error, 4),
                "source_clear_verified": False,
                "source_clear_reason": visual_hold.reason,
                "evidence_object": visual_hold.object_name,
                "visual_source_mad": (
                    round(float(visual_hold.current_source_mad), 4)
                    if visual_hold.current_source_mad is not None
                    else None
                ),
                "visual_source_to_after_mad": (
                    round(float(visual_hold.current_to_after_mad), 4)
                    if visual_hold.current_to_after_mad is not None
                    else None
                ),
                "gripper_state_before": state_before.value,
                "ee_pos": [round(float(v), 4) for v in ee_before_release],
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    ctrl.set_gripper(close=False)
    gap_after, state_after = ctrl.read_gripper_state()
    if state_after is not GripperState.OPEN:
        ctrl.set_gripper(close=False)
        gap_after, state_after = ctrl.read_gripper_state()
    gripper_opened = state_after is GripperState.OPEN
    if not gripper_opened:
        ee_after_open, _, _ = ctrl.read_pose()
        return (
            {
                "ok": False,
                "reached": True,
                "released": False,
                "gripper_opened": False,
                "object_release_verified": None,
                "reason": "release command did not open the gripper",
                "pre_release_error": round(pre_release_error, 4),
                "pre_release_z_error": round(pre_release_z_error, 4),
                "source_clear_verified": True,
                "gripper_hold_continuity": True,
                "source_clear_reason": visual_hold.reason,
                "evidence_object": visual_hold.object_name,
                "visual_source_mad": round(
                    float(visual_hold.current_source_mad),
                    4,
                ),
                "gripper_state_before": state_before.value,
                "gripper_state_pre_release": state_pre_release.value,
                "gripper_state_after": state_after.value,
                "gripper_gap_after": round(float(gap_after), 4),
                "ee_pos": [
                    round(float(v), 4)
                    for v in np.asarray(ee_after_open, dtype=float)
                ],
                **_target_fields(target),
            },
            env.take_snapshot(),
        )

    _clear_surface_target_cache(env)
    from roborsi.embodied.skills.base._lib.libero.visual_hold import (
        clear_visual_hold,
    )

    clear_visual_hold(env)
    retract_reached, _ = ctrl.servo_to(
        hover_pose,
        quat=held_quat,
        gripper="open",
        max_iters=80,
    )
    ee_final, _, _ = ctrl.read_pose()
    return (
        {
            "ok": bool(retract_reached),
            "reached": True,
            "released": True,
            "gripper_opened": True,
            "object_release_verified": None,
            "approach_correction_attempted": approach_correction_attempted,
            "final_correction_attempted": final_correction_attempted,
            "held_object_offset_xy": (
                [
                    round(float(value), 4)
                    for value in held_object_offset_xy
                ]
                if held_object_offset_xy is not None
                else None
            ),
            "held_object_bottom_offset_z": (
                round(float(held_object_bottom_offset_z), 4)
                if held_object_bottom_offset_z is not None
                else None
            ),
            "release_bottom_clearance": round(
                float(release_clearance),
                4,
            ),
            "reason": (
                "released and retracted"
                if retract_reached
                else "released but retract did not reach the hover pose"
            ),
            "pre_release_error": round(pre_release_error, 4),
            "pre_release_z_error": round(pre_release_z_error, 4),
            "source_clear_verified": True,
            "gripper_hold_continuity": True,
            "source_clear_reason": visual_hold.reason,
            "evidence_object": visual_hold.object_name,
            "visual_source_mad": round(
                float(visual_hold.current_source_mad),
                4,
            ),
            "gripper_state_before": state_before.value,
            "gripper_state_pre_release": state_pre_release.value,
            "gripper_state_after": state_after.value,
            "gripper_gap_before": round(float(gap_before), 4),
            "gripper_gap_pre_release": round(float(gap_pre_release), 4),
            "gripper_gap_after": round(float(gap_after), 4),
            "ee_pos": [
                round(float(v), 4)
                for v in np.asarray(ee_final, dtype=float)
            ],
            **_target_fields(target),
        },
        env.take_snapshot(),
    )
