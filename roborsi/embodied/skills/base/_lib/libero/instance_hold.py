"""Pickup-authenticated visual instance association for LIBERO.

This module consumes only coherent public RGB-D/calibration/proprioception,
point-prompt SAM, measured TCP pose, and a separately rendered known-robot
model. Object names are diagnostic only and are never used for transport-time
association.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import numpy as np

_RENDERER_ATTR = "_roborsi_robot_only_renderer"
_MIN_MASK_PIXELS = 30
_MAX_MASK_FRACTION = 0.20
_MIN_DEPTH_PIXELS = 24
_MAX_ROBOT_OVERLAP = 0.20
_MAX_APPEARANCE_L1 = 0.58
_MAX_CENTER_ERROR_M = 0.075
_MIN_AREA_RATIO = 0.22
_MAX_AREA_RATIO = 4.5
_MIN_EXTENT_RATIO = 0.22
_MAX_EXTENT_RATIO = 4.5
_MIN_ASSOCIATION_SCORE_MARGIN = 0.08
_CANDIDATE_RADIUS_PX = 7
_MAX_REFINEMENT_POSITIVE_ERROR_M = 0.18
_MIN_REFINEMENT_VISIBLE_RETENTION = 0.25
_MAX_ROBOT_NEGATIVE_PROMPTS = 6
_MIN_ROBOT_NEGATIVE_SPACING_PX = 7


def _immutable(value: Any, dtype: Any) -> np.ndarray:
    array = np.array(value, dtype=dtype, copy=True, order="C")
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class PickupInstanceReference:
    token: str
    object_name: str
    identity_verified: bool
    frame_token: str
    reset_generation: int
    camera_name: str
    image_shape: tuple[int, int]
    source_pixel: tuple[int, int]
    pickup_mask: np.ndarray
    appearance_descriptor: np.ndarray
    mask_area: int
    mask_bbox: tuple[int, int, int, int]
    finite_depth_count: int
    depth_median: float
    depth_spread: float
    cloud_centroid_world: np.ndarray
    cloud_extent_world: np.ndarray
    pickup_cloud_local: np.ndarray
    object_offset_local: np.ndarray
    grasp_position: np.ndarray
    grasp_quaternion: np.ndarray
    cloud_consistency_error: float
    provenance: str


@dataclass(frozen=True)
class TrackedInstanceObservation:
    ok: bool
    reason: str
    token: str
    frame_token: str | None = None
    mask: np.ndarray | None = None
    robot_mask: np.ndarray | None = None
    cloud_world: np.ndarray | None = None
    predicted_center_world: np.ndarray | None = None
    predicted_pixel: tuple[int, int] | None = None
    metrics: dict[str, float] | None = None


def mask_world_cloud(frame: Any, mask: Any) -> np.ndarray | None:
    if not frame.valid or frame.depth_m is None:
        return None
    selected = np.asarray(mask, dtype=bool)
    depth = np.asarray(frame.depth_m, dtype=float)
    if selected.shape != depth.shape:
        return None
    valid = selected & np.isfinite(depth) & (depth > 0.0)
    rows, cols = np.nonzero(valid)
    if len(rows) == 0:
        return None
    pixels = np.stack(
        [cols.astype(float), rows.astype(float), np.ones(len(rows))],
        axis=0,
    )
    intrinsic = np.asarray(frame.calibration.intrinsic, dtype=float)
    camera_to_world = np.asarray(
        frame.calibration.camera_to_world,
        dtype=float,
    )
    try:
        rays = np.linalg.solve(intrinsic, pixels)
    except np.linalg.LinAlgError:
        return None
    camera = rays * depth[rows, cols][None, :]
    homogeneous = np.vstack([camera, np.ones(camera.shape[1])])
    world = (camera_to_world @ homogeneous)[:3].T
    world = world[np.all(np.isfinite(world), axis=1)]
    return world if len(world) else None


def _refine_mask_with_robot_negative_prompts(
    frame: Any,
    candidate_mask: np.ndarray,
    robot_mask: np.ndarray,
    predicted_center_world: np.ndarray,
) -> tuple[np.ndarray | None, dict[str, float]]:
    """Refine a robot-contaminated SAM mask on the same coherent frame.

    The positive prompt is selected only from visible, finite-depth, nonrobot
    pixels whose backprojected point is close to the proprioceptively predicted
    held-object center. Negative prompts are interior pixels where the original
    candidate overlaps independently rendered robot geometry. The returned mask
    is a fresh SAM proposal, not a mask produced by carving away the robot.
    """
    metrics: dict[str, float] = {
        "refinement_attempted": 1.0,
        "refinement_succeeded": 0.0,
        "refinement_negative_prompt_count": 0.0,
    }
    raw = np.asarray(candidate_mask, dtype=bool)
    robot = np.asarray(robot_mask, dtype=bool)
    depth = np.asarray(frame.depth_m, dtype=float)
    predicted = np.asarray(predicted_center_world, dtype=float)
    if (
        raw.shape != depth.shape
        or robot.shape != raw.shape
        or predicted.shape != (3,)
        or not np.all(np.isfinite(predicted))
    ):
        return None, metrics

    visible = raw & ~robot & np.isfinite(depth) & (depth > 0.0)
    rows, cols = np.nonzero(visible)
    if len(rows) < _MIN_DEPTH_PIXELS:
        return None, metrics
    pixels = np.stack(
        [cols.astype(float), rows.astype(float), np.ones(len(rows))],
        axis=0,
    )
    try:
        rays = np.linalg.solve(
            np.asarray(frame.calibration.intrinsic, dtype=float),
            pixels,
        )
    except np.linalg.LinAlgError:
        return None, metrics
    camera = rays * depth[rows, cols][None, :]
    homogeneous = np.vstack([camera, np.ones(camera.shape[1])])
    world = (
        np.asarray(frame.calibration.camera_to_world, dtype=float)
        @ homogeneous
    )[:3].T
    finite = np.all(np.isfinite(world), axis=1)
    if not np.any(finite):
        return None, metrics
    rows = rows[finite]
    cols = cols[finite]
    world = world[finite]

    # Prefer a point away from the rendered-robot boundary when one exists, then
    # choose the geometrically closest visible point to the predicted center.
    import cv2

    nonrobot_clearance = cv2.distanceTransform(
        (~robot).astype(np.uint8),
        cv2.DIST_L2,
        3,
    )
    clear = nonrobot_clearance[rows, cols] >= 2.0
    eligible = np.nonzero(clear)[0] if np.any(clear) else np.arange(len(rows))
    errors = np.linalg.norm(world[eligible] - predicted[None, :], axis=1)
    best = int(eligible[int(np.argmin(errors))])
    positive_error = float(np.linalg.norm(world[best] - predicted))
    metrics["refinement_positive_world_error_m"] = positive_error
    metrics["refinement_positive_u"] = float(cols[best])
    metrics["refinement_positive_v"] = float(rows[best])
    if positive_error > _MAX_REFINEMENT_POSITIVE_ERROR_M:
        return None, metrics
    positive = int(cols[best]), int(rows[best])

    overlap = raw & robot
    overlap_rows, overlap_cols = np.nonzero(overlap)
    if len(overlap_rows) == 0:
        return None, metrics
    robot_interior = cv2.distanceTransform(
        robot.astype(np.uint8),
        cv2.DIST_L2,
        3,
    )
    order = np.argsort(
        robot_interior[overlap_rows, overlap_cols]
    )[::-1]
    negatives: list[tuple[int, int]] = []
    for index in order:
        point = int(overlap_cols[index]), int(overlap_rows[index])
        if all(
            (point[0] - previous[0]) ** 2
            + (point[1] - previous[1]) ** 2
            >= _MIN_ROBOT_NEGATIVE_SPACING_PX ** 2
            for previous in negatives
        ):
            negatives.append(point)
        if len(negatives) >= _MAX_ROBOT_NEGATIVE_PROMPTS:
            break
    if not negatives:
        return None, metrics
    metrics["refinement_negative_prompt_count"] = float(len(negatives))

    try:
        import torch
        from PIL import Image

        from roborsi.embodied.skills.base._lib.libero._perception import (
            _load_point_sam,
        )

        processor, model = _load_point_sam()
        prompt_points = [positive, *negatives]
        prompt_labels = [1, *([0] * len(negatives))]
        inputs = processor(
            images=Image.fromarray(np.asarray(frame.rgb)),
            input_points=[[list(point) for point in prompt_points]],
            input_labels=[prompt_labels],
            return_tensors="pt",
        ).to(model.device)
        with torch.no_grad():
            output = model(**inputs)
        proposals = processor.image_processor.post_process_masks(
            output.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )[0][0].numpy()
        scores = output.iou_scores.cpu().numpy().reshape(-1)
    except Exception:  # noqa: BLE001
        return None, metrics

    proposals = np.asarray(proposals, dtype=bool)
    if proposals.ndim == 2:
        proposals = proposals[None, ...]
    if proposals.ndim != 3 or proposals.shape[1:] != raw.shape:
        return None, metrics
    original_visible = raw & ~robot
    original_visible_count = int(original_visible.sum())
    accepted: list[tuple[float, float, np.ndarray]] = []
    for index, proposal in enumerate(proposals):
        area = int(proposal.sum())
        if (
            area < _MIN_MASK_PIXELS
            or area / float(proposal.size) > _MAX_MASK_FRACTION
            or not proposal[positive[1], positive[0]]
            or any(proposal[v, u] for u, v in negatives)
        ):
            continue
        retained = int(np.logical_and(proposal, original_visible).sum()) / float(
            max(1, original_visible_count)
        )
        if retained < _MIN_REFINEMENT_VISIBLE_RETENTION:
            continue
        overlap_fraction = int(np.logical_and(proposal, robot).sum()) / float(area)
        model_score = float(scores[index]) if index < len(scores) else 0.0
        accepted.append((overlap_fraction, -model_score, proposal))
    if not accepted:
        return None, metrics
    accepted.sort(key=lambda item: (item[0], item[1]))
    refined_overlap, negative_score, refined = accepted[0]
    retained = int(np.logical_and(refined, original_visible).sum()) / float(
        max(1, original_visible_count)
    )
    metrics.update(
        {
            "refinement_succeeded": 1.0,
            "refinement_model_score": float(-negative_score),
            "refinement_visible_retention": float(retained),
            "refinement_robot_overlap": float(refined_overlap),
            "refinement_raw_mask_area": float(refined.sum()),
        }
    )
    return refined.copy(), metrics


def _descriptor(rgb: Any, mask: Any) -> np.ndarray | None:
    image = np.asarray(rgb)
    selected = np.asarray(mask, dtype=bool)
    if image.ndim != 3 or image.shape[2] != 3 or selected.shape != image.shape[:2]:
        return None
    values = image[selected]
    if len(values) < _MIN_DEPTH_PIXELS:
        return None
    parts = []
    for channel in range(3):
        hist, _ = np.histogram(
            values[:, channel],
            bins=8,
            range=(0, 256),
        )
        parts.append(hist.astype(float))
    result = np.concatenate(parts)
    total = float(result.sum())
    if total <= 0.0:
        return None
    return result / total


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return None
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


def _robust_geometry(cloud: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centroid = np.median(cloud, axis=0)
    low = np.percentile(cloud, 10, axis=0)
    high = np.percentile(cloud, 90, axis=0)
    extent = np.maximum(high - low, 0.003)
    return centroid, np.sort(extent)


def _project_world(frame: Any, point_world: Any) -> tuple[int, int] | None:
    point = np.asarray(point_world, dtype=float)
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return None
    try:
        world_to_camera = np.linalg.inv(frame.calibration.camera_to_world)
    except np.linalg.LinAlgError:
        return None
    camera = world_to_camera @ np.r_[point, 1.0]
    if not np.all(np.isfinite(camera)) or camera[2] <= 0.0:
        return None
    pixel = frame.calibration.intrinsic @ camera[:3]
    u = int(round(float(pixel[0] / pixel[2])))
    v = int(round(float(pixel[1] / pixel[2])))
    if not (
        0 <= u < frame.calibration.image_width
        and 0 <= v < frame.calibration.image_height
    ):
        return None
    return u, v


def robot_projection_for_frame(env: Any, frame: Any):
    from roborsi.embodied.skills.base._lib.libero.robot_projection import (
        RobotOnlyRenderer,
    )

    renderer = getattr(env, _RENDERER_ATTR, None)
    geometry = env.known_robot_geometry()
    if (
        renderer is None
        or getattr(renderer, "_reset", None) != frame.reset_generation
    ):
        if renderer is not None:
            try:
                renderer.close()
            except Exception:  # noqa: BLE001
                pass
        renderer = RobotOnlyRenderer(geometry, frame.calibration)
        setattr(env, _RENDERER_ATTR, renderer)
    return renderer.render(
        frame,
        depth_tolerance_m=0.008,
        dilation_pixels=2,
    )


def build_pickup_reference(
    env: Any,
    *,
    frame: Any,
    object_name: str,
    source_pixel: tuple[int, int],
    accepted_cloud: Any,
    grasp_position: Any,
    grasp_quaternion: Any,
    object_offset_local: Any,
    identity_verified: bool,
) -> PickupInstanceReference | None:
    if not identity_verified or not frame.valid:
        return None
    u, v = int(source_pixel[0]), int(source_pixel[1])
    if not (
        0 <= u < frame.calibration.image_width
        and 0 <= v < frame.calibration.image_height
    ):
        return None
    from roborsi.embodied.skills.base._lib.libero._perception import (
        sam_mask_at_point,
    )

    projection = robot_projection_for_frame(env, frame)
    mask = np.asarray(sam_mask_at_point(frame.rgb, u, v), dtype=bool)
    robot = np.asarray(projection.robot_mask, dtype=bool)
    if mask.shape != frame.depth_m.shape or robot.shape != mask.shape:
        return None
    if not mask[v, u] or robot[v, u]:
        return None
    raw_area = int(mask.sum())
    if raw_area < _MIN_MASK_PIXELS or raw_area / float(mask.size) > _MAX_MASK_FRACTION:
        return None
    robot_overlap = int(np.logical_and(mask, robot).sum()) / float(raw_area)
    if robot_overlap > 0.05:
        return None
    mask = mask & ~robot
    cloud = mask_world_cloud(frame, mask)
    if cloud is None or len(cloud) < _MIN_DEPTH_PIXELS:
        return None
    accepted = np.asarray(accepted_cloud, dtype=float)
    if accepted.ndim != 2 or accepted.shape[1] < 3:
        return None
    accepted = accepted[
        np.all(np.isfinite(accepted[:, :3]), axis=1),
        :3,
    ]
    if len(accepted) < 3:
        return None
    centroid, extent = _robust_geometry(cloud)
    accepted_centroid = np.median(accepted, axis=0)
    consistency = float(np.linalg.norm(centroid - accepted_centroid))
    if not np.isfinite(consistency) or consistency > 0.06:
        return None
    descriptor = _descriptor(frame.rgb, mask)
    bounds = _bbox(mask)
    position = np.asarray(grasp_position, dtype=float)
    quaternion = np.asarray(grasp_quaternion, dtype=float)
    offset = np.asarray(object_offset_local, dtype=float)
    if (
        descriptor is None
        or bounds is None
        or position.shape != (3,)
        or quaternion.shape != (4,)
        or offset.shape != (3,)
        or not np.all(np.isfinite(position))
        or not np.all(np.isfinite(quaternion))
        or not np.all(np.isfinite(offset))
        or float(np.linalg.norm(quaternion)) <= 0.0
        or float(np.linalg.norm(offset)) > 0.12
    ):
        return None
    depth_values = np.asarray(frame.depth_m, dtype=float)[mask]
    depth_values = depth_values[np.isfinite(depth_values) & (depth_values > 0.0)]
    if len(depth_values) < _MIN_DEPTH_PIXELS:
        return None
    quaternion = quaternion / np.linalg.norm(quaternion)
    try:
        from scipy.spatial.transform import Rotation

        pickup_cloud_local = Rotation.from_quat(quaternion).inv().apply(
            cloud - position[None, :]
        )
        authenticated_offset_local = Rotation.from_quat(quaternion).inv().apply(
            centroid - position
        )
    except (TypeError, ValueError):
        return None
    if (
        pickup_cloud_local.ndim != 2
        or pickup_cloud_local.shape[1] != 3
        or len(pickup_cloud_local) < _MIN_DEPTH_PIXELS
        or not np.all(np.isfinite(pickup_cloud_local))
        or authenticated_offset_local.shape != (3,)
        or not np.all(np.isfinite(authenticated_offset_local))
        or float(np.linalg.norm(authenticated_offset_local)) > 0.12
    ):
        return None
    immutable_mask = _immutable(mask, bool)
    return PickupInstanceReference(
        token=uuid4().hex,
        object_name=str(object_name or "").strip(),
        identity_verified=True,
        frame_token=str(frame.frame_token),
        reset_generation=int(frame.reset_generation),
        camera_name=str(frame.calibration.camera_name),
        image_shape=tuple(int(x) for x in mask.shape),
        source_pixel=(u, v),
        pickup_mask=immutable_mask,
        appearance_descriptor=_immutable(descriptor, float),
        mask_area=int(mask.sum()),
        mask_bbox=bounds,
        finite_depth_count=int(len(depth_values)),
        depth_median=float(np.median(depth_values)),
        depth_spread=float(
            np.percentile(depth_values, 90)
            - np.percentile(depth_values, 10)
        ),
        cloud_centroid_world=_immutable(centroid, float),
        cloud_extent_world=_immutable(extent, float),
        pickup_cloud_local=_immutable(pickup_cloud_local, float),
        object_offset_local=_immutable(authenticated_offset_local, float),
        grasp_position=_immutable(position, float),
        grasp_quaternion=_immutable(quaternion, float),
        cloud_consistency_error=consistency,
        provenance="identity_grounded_pixel+point_sam+coherent_rgbd",
    )


def _deduplicate_masks(masks: list[np.ndarray]) -> list[np.ndarray]:
    unique: list[np.ndarray] = []
    for candidate in masks:
        duplicate = False
        for previous in unique:
            union = int(np.logical_or(candidate, previous).sum())
            intersection = int(np.logical_and(candidate, previous).sum())
            smaller = min(int(candidate.sum()), int(previous.sum()))
            if union and (
                intersection / float(union) >= 0.75
                or (
                    smaller > 0
                    and intersection / float(smaller) >= 0.85
                )
            ):
                # Nearby point prompts commonly produce slightly expanded or
                # nested masks for the same physical instance. Treat those as
                # one candidate; disjoint masks remain independent alternatives.
                duplicate = True
                break
        if not duplicate:
            unique.append(candidate)
    return unique


def associate_pickup_reference(
    env: Any,
    reference: PickupInstanceReference,
    *,
    holding: bool,
    frame: Any | None = None,
) -> TrackedInstanceObservation:
    if holding is not True:
        return TrackedInstanceObservation(False, "hold_not_confirmed", reference.token)
    if not reference.identity_verified:
        return TrackedInstanceObservation(False, "reference_not_verified", reference.token)
    if frame is None:
        try:
            frame = env.coherent_sensor_frame("agentview")
        except Exception:  # noqa: BLE001
            return TrackedInstanceObservation(
                False,
                "frame_unavailable",
                reference.token,
            )
    if (
        not frame.valid
        or frame.reset_generation != reference.reset_generation
        or frame.calibration.camera_name != reference.camera_name
        or tuple(frame.depth_m.shape) != reference.image_shape
    ):
        return TrackedInstanceObservation(
            False,
            "stale_or_invalid_frame",
            reference.token,
            getattr(frame, "frame_token", None),
        )
    try:
        projection = robot_projection_for_frame(env, frame)
    except Exception:  # noqa: BLE001
        return TrackedInstanceObservation(
            False,
            "robot_projection_unavailable",
            reference.token,
            frame.frame_token,
        )
    from scipy.spatial.transform import Rotation
    from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
    from roborsi.embodied.skills.base._lib.libero._perception import (
        sam_mask_at_point,
    )

    try:
        tcp_position, tcp_quaternion, _ = LiberoControl(env).read_pose()
        tcp_position = np.asarray(tcp_position, dtype=float)
        tcp_quaternion = np.asarray(tcp_quaternion, dtype=float)
        predicted = tcp_position + Rotation.from_quat(tcp_quaternion).apply(
            reference.object_offset_local
        )
    except Exception:  # noqa: BLE001
        return TrackedInstanceObservation(
            False,
            "tcp_pose_unavailable",
            reference.token,
            frame.frame_token,
            robot_mask=projection.robot_mask,
        )
    pixel = _project_world(frame, predicted)
    if pixel is None:
        return TrackedInstanceObservation(
            False,
            "predicted_instance_not_visible",
            reference.token,
            frame.frame_token,
            robot_mask=projection.robot_mask,
            predicted_center_world=_immutable(predicted, float),
        )
    offsets = (
        (0, 0),
        (_CANDIDATE_RADIUS_PX, 0),
        (-_CANDIDATE_RADIUS_PX, 0),
        (0, _CANDIDATE_RADIUS_PX),
        (0, -_CANDIDATE_RADIUS_PX),
        (_CANDIDATE_RADIUS_PX, _CANDIDATE_RADIUS_PX),
        (_CANDIDATE_RADIUS_PX, -_CANDIDATE_RADIUS_PX),
        (-_CANDIDATE_RADIUS_PX, _CANDIDATE_RADIUS_PX),
        (-_CANDIDATE_RADIUS_PX, -_CANDIDATE_RADIUS_PX),
    )
    candidates: list[np.ndarray] = []
    for du, dv in offsets:
        u, v = pixel[0] + du, pixel[1] + dv
        if not (0 <= u < reference.image_shape[1] and 0 <= v < reference.image_shape[0]):
            continue
        try:
            mask = np.asarray(sam_mask_at_point(frame.rgb, u, v), dtype=bool)
        except Exception:  # noqa: BLE001
            continue
        if mask.shape == reference.image_shape and mask[v, u]:
            candidates.append(mask)
    candidates = _deduplicate_masks(candidates)
    robot = np.asarray(projection.robot_mask, dtype=bool)
    evaluation_candidates: list[tuple[np.ndarray, dict[str, float]]] = []
    for candidate in candidates:
        original_area = int(candidate.sum())
        original_overlap = (
            int(np.logical_and(candidate, robot).sum()) / float(original_area)
            if original_area > 0
            else 1.0
        )
        original_metrics: dict[str, float] = {
            "segmentation_refined": 0.0,
            "original_raw_mask_area": float(original_area),
            "original_robot_overlap": float(original_overlap),
        }
        refined = None
        refinement_metrics: dict[str, float] = {}
        if original_area > 0 and original_overlap > _MAX_ROBOT_OVERLAP:
            refined, refinement_metrics = _refine_mask_with_robot_negative_prompts(
                frame,
                candidate,
                robot,
                predicted,
            )
            original_metrics.update(refinement_metrics)
        evaluation_candidates.append((candidate, original_metrics))
        if refined is not None:
            refined_metrics = dict(refinement_metrics)
            refined_metrics.update(
                {
                    "segmentation_refined": 1.0,
                    "original_raw_mask_area": float(original_area),
                    "original_robot_overlap": float(original_overlap),
                }
            )
            evaluation_candidates.append((refined, refined_metrics))

    passing: list[tuple[float, np.ndarray, np.ndarray, dict[str, float]]] = []
    evaluated: list[tuple[float, np.ndarray, np.ndarray, dict[str, float]]] = []
    for raw_mask, segmentation_metrics in evaluation_candidates:
        raw_area = int(raw_mask.sum())
        if raw_area < _MIN_MASK_PIXELS or raw_area / float(raw_mask.size) > _MAX_MASK_FRACTION:
            continue
        overlap = int(np.logical_and(raw_mask, robot).sum()) / float(raw_area)
        mask = raw_mask & ~robot
        area = int(mask.sum())
        area_ratio = area / float(max(1, reference.mask_area))
        descriptor = _descriptor(frame.rgb, mask)
        cloud = mask_world_cloud(frame, mask)
        if descriptor is None or cloud is None or len(cloud) < _MIN_DEPTH_PIXELS:
            continue
        centroid, extent = _robust_geometry(cloud)
        appearance = float(
            np.abs(descriptor - reference.appearance_descriptor).sum()
        )
        center_error = float(np.linalg.norm(centroid - predicted))
        extent_ratio_values = extent / reference.cloud_extent_world
        extent_min = float(np.min(extent_ratio_values))
        extent_max = float(np.max(extent_ratio_values))
        center_limit = max(
            _MAX_CENTER_ERROR_M,
            2.5 * float(np.max(reference.cloud_extent_world)),
        )
        score = (
            1.0
            - min(1.0, appearance / _MAX_APPEARANCE_L1) * 0.35
            - min(1.0, center_error / _MAX_CENTER_ERROR_M) * 0.35
            - min(1.0, overlap / _MAX_ROBOT_OVERLAP) * 0.20
            - min(1.0, abs(float(np.log(area_ratio))) / 1.5) * 0.10
        )
        metrics = {
            "raw_mask_area": float(raw_area),
            "nonrobot_mask_area": float(area),
            "robot_overlap": overlap,
            "area_ratio": area_ratio,
            "appearance_l1": appearance,
            "center_error_m": center_error,
            "center_error_limit_m": float(center_limit),
            "extent_ratio_min": extent_min,
            "extent_ratio_max": extent_max,
            "finite_depth_count": float(len(cloud)),
            "score": float(score),
            "passes_robot_overlap": float(overlap <= _MAX_ROBOT_OVERLAP),
            "passes_area_ratio": float(
                _MIN_AREA_RATIO <= area_ratio <= _MAX_AREA_RATIO
            ),
            "passes_appearance": float(appearance <= _MAX_APPEARANCE_L1),
            "passes_center_error": float(center_error <= center_limit),
            "passes_extent": float(
                extent_min >= _MIN_EXTENT_RATIO
                and extent_max <= _MAX_EXTENT_RATIO
            ),
            **segmentation_metrics,
        }
        evaluated.append((score, mask, cloud, metrics))
        if not (
            overlap <= _MAX_ROBOT_OVERLAP
            and _MIN_AREA_RATIO <= area_ratio <= _MAX_AREA_RATIO
            and appearance <= _MAX_APPEARANCE_L1
            and center_error <= center_limit
            and extent_min >= _MIN_EXTENT_RATIO
            and extent_max <= _MAX_EXTENT_RATIO
        ):
            continue
        passing.append((score, mask, cloud, metrics))
    if not passing:
        diagnostic_mask = None
        diagnostic_cloud = None
        diagnostic_metrics: dict[str, float] = {
            "prompt_candidate_count": float(len(candidates)),
            "evaluated_candidate_count": float(len(evaluated)),
            "passing_candidate_count": 0.0,
        }
        if evaluated:
            evaluated.sort(key=lambda item: item[0], reverse=True)
            _, diagnostic_mask, diagnostic_cloud, best_metrics = evaluated[0]
            diagnostic_metrics.update(best_metrics)
        return TrackedInstanceObservation(
            False,
            "mismatch_or_occluded",
            reference.token,
            frame.frame_token,
            mask=(
                None
                if diagnostic_mask is None
                else _immutable(diagnostic_mask, bool)
            ),
            robot_mask=_immutable(robot, bool),
            cloud_world=(
                None
                if diagnostic_cloud is None
                else _immutable(diagnostic_cloud, float)
            ),
            predicted_center_world=_immutable(predicted, float),
            predicted_pixel=pixel,
            metrics=diagnostic_metrics,
        )
    passing.sort(key=lambda item: item[0], reverse=True)
    score, mask, cloud, top_metrics = passing[0]
    metrics = dict(top_metrics)
    if len(passing) > 1:
        runner_up_score = float(passing[1][0])
        score_margin = float(score - runner_up_score)
        metrics["runner_up_score"] = runner_up_score
        metrics["score_margin"] = score_margin
        if score_margin < _MIN_ASSOCIATION_SCORE_MARGIN:
            return TrackedInstanceObservation(
                False,
                "ambiguous",
                reference.token,
                frame.frame_token,
                robot_mask=_immutable(robot, bool),
                predicted_center_world=_immutable(predicted, float),
                predicted_pixel=pixel,
                metrics=metrics,
            )
    else:
        metrics["runner_up_score"] = -1.0
        metrics["score_margin"] = float(score + 1.0)
    if score < 0.35:
        return TrackedInstanceObservation(
            False,
            "low_confidence",
            reference.token,
            frame.frame_token,
            robot_mask=_immutable(robot, bool),
            predicted_center_world=_immutable(predicted, float),
            predicted_pixel=pixel,
            metrics=metrics,
        )
    return TrackedInstanceObservation(
        True,
        "accepted",
        reference.token,
        frame.frame_token,
        mask=_immutable(mask, bool),
        robot_mask=_immutable(robot, bool),
        cloud_world=_immutable(cloud, float),
        predicted_center_world=_immutable(predicted, float),
        predicted_pixel=pixel,
        metrics=metrics,
    )
