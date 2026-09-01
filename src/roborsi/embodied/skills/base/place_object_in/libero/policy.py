"""place_object_in — composite place of the held object (base/libero).

Two modes:
  * container drop — ``object=<name>`` that has a ``<name>_contain_region`` site
    (e.g. basket_1): release the held object from ABOVE the region so it falls
    in. The gripper never enters the container — descending into a light
    container just shoves it out of the way.
  * surface / coordinate place — ``pos=[x,y,z]`` or an ``object`` with no contain
    region: hover, descend to the release point, open, retract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import (
    LiberoControl,
    bounded_residual_correction_target,
)
from roborsi.embodied.skills.base._lib.libero.drawer_evidence import (
    get_drawer_pull_evidence,
    is_drawer_target,
    resolve_open_drawer_point,
)
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    get_visual_hold,
    verify_visual_hold,
)

# A held object hangs below the grip_site; release this far ABOVE the perceived
# target surface so the object clears the table and drops onto the target instead
# of the arm ramming it through the surface and wedging short.
_RELEASE_GAP = 0.06
_APPROACH_POS_TOL = 0.06
_APPROACH_Z_TOL = 0.06
_FINAL_POS_TOL = 0.06
_FINAL_Z_TOL = 0.02
_ADAPTIVE_MAX_XY_ERROR = 0.04
_ADAPTIVE_MIN_Z_CLEARANCE = 0.025
_ADAPTIVE_MAX_Z_CLEARANCE = 0.055
_MIN_CONTAINER_POINTS = 20
_MIN_CONTAINER_SPAN = 0.025
_MAX_CONTAINER_SPAN = 0.45
_CONTAINER_PIXEL_PADDING = 0.025
_CONTAINER_PIXEL_Z_PADDING = 0.08
_CONTAINER_INTERIOR_MARGIN = 0.006
_MIN_POST_RELEASE_OBJECT_POINTS = 12
_MIN_BELOW_RIM_FRACTION = 0.08
_BELOW_RIM_MARGIN = 0.003
_MAX_HOLD_OFFSET_NORM = 0.10
_MAX_HOLD_OFFSET_COMPONENT = 0.08
_RELATIONAL_TARGET_WORDS = {
    "back",
    "rear",
    "front",
    "left",
    "right",
    "top",
    "bottom",
    "upper",
    "lower",
    "middle",
    "compartment",
    "section",
    "slot",
}


@dataclass(frozen=True)
class _ContainerGeometry:
    pixel: tuple[int, int]
    origin_xy: np.ndarray
    axes: np.ndarray
    lower_xy: np.ndarray
    upper_xy: np.ndarray
    center_xy: np.ndarray
    floor_z: float
    rim_z: float
    point_count: int
    pixel_supported: bool | None


def _bad_uv(uv) -> bool:
    """None, or the (128,128) 'found nothing' sentinel on a 256px LIBERO frame."""
    if uv is None:
        return True
    try:
        return abs(int(uv[0]) - 128) <= 2 and abs(int(uv[1]) - 128) <= 2
    except (TypeError, ValueError, OverflowError, IndexError):
        return True


def _requires_precise_pixel(target_name: str) -> bool:
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in str(target_name or "").split()
    }
    return bool(words & _RELATIONAL_TARGET_WORDS)


def _clear_localize(state, ctrl, name: str):
    """Localize a place target with the head view cleared (mirror of the grasp
    self-correct): try; if nothing / sentinel, side-step the arm and retry once.
    Pure vision, no ground truth."""
    from roborsi.embodied.skills.base._lib.libero._perception import localize_precise
    loc = localize_precise(state, name)
    if not _bad_uv(loc):
        return loc
    ee, _, _ = ctrl.read_pose()
    reached, _ = ctrl.servo_to(
        [float(ee[0]) - 0.06, float(ee[1]) + 0.08, float(ee[2])],
        gripper="close",
        max_iters=40,
    )
    if not reached:
        return None
    loc = localize_precise(state, name)
    return None if _bad_uv(loc) else loc


def _motion_fields(prefix: str, target, measured) -> dict[str, Any]:
    try:
        point = np.asarray(measured, dtype=float)
    except (TypeError, ValueError, OverflowError):
        point = np.array([])
    valid = point.shape == (3,) and np.all(np.isfinite(point))
    position_error = (
        float(np.linalg.norm(point - target)) if valid else float("inf")
    )
    z_error = abs(float(point[2] - target[2])) if valid else float("inf")
    return {
        f"{prefix}_position_error": (
            round(position_error, 4) if np.isfinite(position_error) else None
        ),
        f"{prefix}_z_error": round(z_error, 4) if np.isfinite(z_error) else None,
        "ee_pos": [round(float(v), 4) for v in point] if valid else None,
    }


def _motion_within(target, measured, pos_tol: float, z_tol: float) -> bool:
    try:
        point = np.asarray(measured, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        point.shape == (3,)
        and np.all(np.isfinite(point))
        and float(np.linalg.norm(point - target)) <= pos_tol
        and abs(float(point[2] - target[2])) <= z_tol
    )


def _bounded_container_clearance(target, measured) -> bool:
    try:
        point = np.asarray(measured, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return False
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return False
    delta = point - np.asarray(target, dtype=float)
    return bool(
        float(np.linalg.norm(delta[:2])) <= _ADAPTIVE_MAX_XY_ERROR
        and _ADAPTIVE_MIN_Z_CLEARANCE
        <= float(delta[2])
        <= _ADAPTIVE_MAX_Z_CLEARANCE
    )


def _finite_cloud(value: Any, *, min_points: int) -> np.ndarray | None:
    if value is None:
        return None
    try:
        cloud = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return None
    cloud = cloud[np.all(np.isfinite(cloud), axis=1)]
    return cloud if len(cloud) >= min_points else None


def _depth_neighborhood_drop(
    env: Any,
    u: int,
    v: int,
) -> np.ndarray | None:
    points = []
    center = env.pixel_to_world(int(u), int(v))
    try:
        center_point = np.asarray(center, dtype=float)
    except (TypeError, ValueError, OverflowError):
        center_point = np.array([])
    if center_point.shape != (3,) or not np.all(np.isfinite(center_point)):
        return None
    for du, dv in (
        (0, 0),
        (-6, 0),
        (6, 0),
        (0, -6),
        (0, 6),
        (-6, -6),
        (-6, 6),
        (6, -6),
        (6, 6),
    ):
        value = env.pixel_to_world(int(u + du), int(v + dv))
        try:
            point = np.asarray(value, dtype=float)
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            point.shape == (3,)
            and np.all(np.isfinite(point))
            and float(np.linalg.norm(point - center_point)) <= 0.18
        ):
            points.append(point)
    if not points:
        return None
    cloud = np.asarray(points, dtype=float)
    return np.asarray(
        [
            float(np.median(cloud[:, 0])),
            float(np.median(cloud[:, 1])),
            float(np.percentile(cloud[:, 2], 80)),
        ],
        dtype=float,
    )


def _container_geometry_from_cloud(
    cloud_value: Any,
    *,
    pixel: tuple[int, int],
    pixel_world: Any,
) -> tuple[_ContainerGeometry | None, str | None]:
    cloud = _finite_cloud(
        cloud_value,
        min_points=_MIN_CONTAINER_POINTS,
    )
    if cloud is None:
        return None, (
            "could not perceive a reliable container rim; "
            "single-pixel depth is not sufficient for release"
        )

    xy = cloud[:, :2]
    origin = np.median(xy, axis=0)
    centered = xy - origin
    covariance = np.cov(centered, rowvar=False)
    if covariance.shape != (2, 2) or not np.all(np.isfinite(covariance)):
        return None, "container cloud has invalid planar geometry"
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    axes = eigenvectors[:, order]
    if (
        not np.all(np.isfinite(eigenvalues))
        or float(eigenvalues[order[-1]]) <= 1e-8
    ):
        return None, "container cloud has degenerate planar geometry"
    if float(np.linalg.det(axes)) < 0.0:
        axes[:, 1] *= -1.0

    projected = centered @ axes
    lower = np.percentile(projected, 5, axis=0)
    upper = np.percentile(projected, 95, axis=0)
    spans = upper - lower
    if (
        not np.all(np.isfinite(spans))
        or np.any(spans < _MIN_CONTAINER_SPAN)
        or np.any(spans > _MAX_CONTAINER_SPAN)
    ):
        return None, "container cloud footprint is not reliable"

    floor_z = float(np.percentile(cloud[:, 2], 10))
    rim_z = float(np.percentile(cloud[:, 2], 85))
    if (
        not np.isfinite(floor_z)
        or not np.isfinite(rim_z)
        or rim_z - floor_z < 0.008
        or rim_z - floor_z > 0.25
    ):
        return None, "container rim height is not reliable"

    center_local = (lower + upper) / 2.0
    center_xy = origin + center_local @ axes.T
    world = None
    try:
        candidate = np.asarray(pixel_world, dtype=float)
        if candidate.shape == (3,) and np.all(np.isfinite(candidate)):
            world = candidate
    except (TypeError, ValueError, OverflowError):
        pass

    pixel_supported = None
    if world is not None:
        local = (world[:2] - origin) @ axes
        xy_supported = bool(
            np.all(local >= lower - _CONTAINER_PIXEL_PADDING)
            and np.all(local <= upper + _CONTAINER_PIXEL_PADDING)
        )
        z_supported = bool(
            floor_z - _CONTAINER_PIXEL_Z_PADDING
            <= float(world[2])
            <= rim_z + _CONTAINER_PIXEL_Z_PADDING
        )
        pixel_supported = xy_supported and z_supported
        if not pixel_supported:
            return None, (
                "container cloud is inconsistent with the requested pixel; "
                "refusing release"
            )

    return (
        _ContainerGeometry(
            pixel=(int(pixel[0]), int(pixel[1])),
            origin_xy=np.asarray(origin, dtype=float),
            axes=np.asarray(axes, dtype=float),
            lower_xy=np.asarray(lower, dtype=float),
            upper_xy=np.asarray(upper, dtype=float),
            center_xy=np.asarray(center_xy, dtype=float),
            floor_z=floor_z,
            rim_z=rim_z,
            point_count=int(len(cloud)),
            pixel_supported=pixel_supported,
        ),
        None,
    )


def _container_fields(
    geometry: _ContainerGeometry | None,
) -> dict[str, Any]:
    if geometry is None:
        return {
            "container_pixel": None,
            "container_center_world": None,
            "container_floor_z": None,
            "container_rim_z": None,
            "container_point_count": 0,
            "container_pixel_supported": None,
        }
    return {
        "container_pixel": list(geometry.pixel),
        "container_center_world": [
            round(float(geometry.center_xy[0]), 4),
            round(float(geometry.center_xy[1]), 4),
            round(float(geometry.rim_z), 4),
        ],
        "container_floor_z": round(float(geometry.floor_z), 4),
        "container_rim_z": round(float(geometry.rim_z), 4),
        "container_point_count": geometry.point_count,
        "container_pixel_supported": geometry.pixel_supported,
    }


def _held_object_world_offset(
    evidence: Any,
    held_quat: np.ndarray | None,
) -> tuple[np.ndarray | None, str | None]:
    raw_offset = getattr(evidence, "object_offset_local", None)
    if raw_offset is None:
        return None, None
    try:
        local_offset = np.asarray(raw_offset, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None, "visual hold object offset is malformed"
    if (
        local_offset.shape != (3,)
        or not np.all(np.isfinite(local_offset))
        or float(np.linalg.norm(local_offset)) > _MAX_HOLD_OFFSET_NORM
        or np.any(np.abs(local_offset) > _MAX_HOLD_OFFSET_COMPONENT)
    ):
        return None, "visual hold object offset is outside safe bounds"
    if held_quat is None:
        return None, (
            "visual hold object offset cannot be applied because the "
            "end-effector orientation is invalid"
        )

    from scipy.spatial.transform import Rotation

    try:
        world_offset = Rotation.from_quat(held_quat).apply(local_offset)
    except (TypeError, ValueError):
        return None, "visual hold object offset rotation is invalid"
    if (
        world_offset.shape != (3,)
        or not np.all(np.isfinite(world_offset))
        or float(np.linalg.norm(world_offset)) > _MAX_HOLD_OFFSET_NORM
        or np.any(np.abs(world_offset) > _MAX_HOLD_OFFSET_COMPONENT)
    ):
        return None, "rotated visual hold object offset is outside safe bounds"
    return np.asarray(world_offset, dtype=float), None


def _unverified_containment(reason: str, **fields) -> dict[str, Any]:
    return {
        "verified": False,
        "placed": False,
        "reason": reason,
        "inside_xy": None,
        "below_rim": None,
        "object_cloud_distinct": None,
        **fields,
    }


def _post_release_containment_from_cloud(
    geometry: _ContainerGeometry,
    object_cloud_value: Any,
    *,
    object_pixel: tuple[int, int],
) -> dict[str, Any]:
    object_cloud = _finite_cloud(
        object_cloud_value,
        min_points=_MIN_POST_RELEASE_OBJECT_POINTS,
    )
    if object_cloud is None:
        return _unverified_containment(
            "released object cloud is unavailable after release",
            object_pixel=list(object_pixel),
        )

    local_xy = (object_cloud[:, :2] - geometry.origin_xy) @ geometry.axes
    object_lower = np.percentile(local_xy, 10, axis=0)
    object_upper = np.percentile(local_xy, 90, axis=0)
    object_spans = object_upper - object_lower
    object_center_local = np.median(local_xy, axis=0)
    object_center_xy = (
        geometry.origin_xy + object_center_local @ geometry.axes.T
    )
    container_spans = geometry.upper_xy - geometry.lower_xy
    margin = np.minimum(
        np.full(2, _CONTAINER_INTERIOR_MARGIN, dtype=float),
        container_spans * 0.12,
    )
    center_inside = bool(
        np.all(object_center_local >= geometry.lower_xy + margin)
        and np.all(object_center_local <= geometry.upper_xy - margin)
    )
    points_inside = np.all(
        (local_xy >= geometry.lower_xy)
        & (local_xy <= geometry.upper_xy),
        axis=1,
    )
    inside_fraction = float(np.mean(points_inside))
    inside_xy = bool(center_inside and inside_fraction >= 0.55)

    object_z_span = float(
        np.percentile(object_cloud[:, 2], 90)
        - np.percentile(object_cloud[:, 2], 10)
    )
    container_z_span = float(geometry.rim_z - geometry.floor_z)
    object_cloud_distinct = not bool(
        np.all(object_spans >= 0.72 * container_spans)
        and object_z_span >= 0.65 * container_z_span
    )

    below_rim_fraction = float(
        np.mean(
            object_cloud[:, 2]
            <= geometry.rim_z - _BELOW_RIM_MARGIN
        )
    )
    below_rim = bool(
        below_rim_fraction >= _MIN_BELOW_RIM_FRACTION
        and float(np.percentile(object_cloud[:, 2], 10))
        < geometry.rim_z - _BELOW_RIM_MARGIN
    )
    placed = bool(inside_xy and below_rim and object_cloud_distinct)
    if not object_cloud_distinct:
        reason = "post-release object cloud is not distinct from container"
    elif not inside_xy:
        reason = "released object is outside the container footprint"
    elif not below_rim:
        reason = "released object is not visibly below the container rim"
    else:
        reason = "released object is visually contained"

    return {
        "verified": True,
        "placed": placed,
        "reason": reason,
        "object_pixel": list(object_pixel),
        "object_point_count": int(len(object_cloud)),
        "object_center_world": [
            round(float(object_center_xy[0]), 4),
            round(float(object_center_xy[1]), 4),
            round(float(np.median(object_cloud[:, 2])), 4),
        ],
        "inside_xy": inside_xy,
        "inside_fraction": round(inside_fraction, 4),
        "below_rim": below_rim,
        "below_rim_fraction": round(below_rim_fraction, 4),
        "object_cloud_distinct": object_cloud_distinct,
        "container_rim_z": round(float(geometry.rim_z), 4),
    }


def _verify_released_object_pixel(
    state: Any,
    object_name: str,
    object_pixel: tuple[int, int],
) -> bool:
    from roborsi.embodied.skills.base.grasp_object.libero.policy import (
        _verify_object_pixel,
    )

    rgb = state.env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return False
    return bool(
        _verify_object_pixel(
            state,
            object_name,
            rgb,
            object_pixel,
        )
    )


def _verify_post_release_containment(
    state: Any,
    *,
    geometry: _ContainerGeometry | None,
    object_name: str,
) -> dict[str, Any]:
    if geometry is None:
        return _unverified_containment(
            "container geometry is unavailable for post-release verification"
        )
    object_name = str(object_name or "").strip()
    if not object_name:
        return _unverified_containment(
            "released object identity is unavailable for visual verification"
        )

    from roborsi.embodied.skills.base._lib.libero._perception import (
        localize_precise,
        object_cloud,
    )

    located = localize_precise(state, object_name)
    if _bad_uv(located):
        return _unverified_containment(
            "released object could not be localized after release"
        )
    object_pixel = (int(located[0]), int(located[1]))
    if not _verify_released_object_pixel(
        state,
        object_name,
        object_pixel,
    ):
        return _unverified_containment(
            "post-release object identity was not visually verified",
            object_pixel=list(object_pixel),
        )
    cloud = object_cloud(
        state.env,
        object_pixel[0],
        object_pixel[1],
        z_band=0.18,
    )
    return _post_release_containment_from_cloud(
        geometry,
        cloud,
        object_pixel=object_pixel,
    )


def _failure(reason: str, **fields) -> dict[str, Any]:
    return {
        "ok": False,
        "reached": False,
        "released": False,
        "placed": False,
        "gripper_opened": False,
        "post_release_visual_containment": None,
        "reason": reason,
        **fields,
    }


def _open_and_verify(ctrl: LiberoControl) -> tuple[bool, float, GripperState]:
    gap = 0.0
    state = GripperState.AMBIGUOUS
    for _ in range(2):
        ctrl.set_gripper(close=False)
        gap, state = ctrl.read_gripper_state()
        if state is GripperState.OPEN:
            return True, float(gap), state
    return False, float(gap), state


def dispatch_runtime(state, args: dict[str, Any]):
    env = state.env
    ctrl = LiberoControl(env)
    hover = float(args.get("hover", 0.12))
    z_offset = float(args.get("z_offset", 0.03))
    release_clearance = _RELEASE_GAP

    pos = args.get("pos")
    pixel = args.get("pixel")
    target_name = str(args.get("object") or "").strip()
    pixel_fallback = None
    drawer_resolution = None

    gap_before, state_before = ctrl.read_gripper_state()
    if state_before is not GripperState.HELD:
        return (
            _failure(
                f"gripper state is {state_before.value}, not a confirmed hold",
                gripper_state_before=state_before.value,
                gripper_gap_before=round(float(gap_before), 4),
            ),
            env.take_snapshot(),
        )
    initial_visual_hold = verify_visual_hold(
        env,
        env.take_snapshot().images.get("head_camera"),
        holding=True,
    )
    if not initial_visual_hold.ok:
        return (
            _failure(
                "visual hold evidence is invalid",
                visual_hold_reason=initial_visual_hold.reason,
            ),
            env.take_snapshot(),
        )

    visual_hold_evidence = get_visual_hold(env)
    held_identity_verified = bool(
        getattr(initial_visual_hold, "identity_verified", False)
        or getattr(visual_hold_evidence, "identity_verified", False)
    )
    if not held_identity_verified:
        return (
            _failure(
                "held object identity is not verified",
                held_identity_verified=False,
            ),
            env.take_snapshot(),
        )
    held_object_name = str(
        getattr(initial_visual_hold, "object_name", None)
        or getattr(visual_hold_evidence, "object_name", None)
        or ""
    ).strip()
    _, raw_held_quat, _ = ctrl.read_pose()
    held_quat = None
    try:
        candidate_quat = np.asarray(raw_held_quat, dtype=float)
        if (
            candidate_quat.shape == (4,)
            and np.all(np.isfinite(candidate_quat))
            and float(np.linalg.norm(candidate_quat)) > 1e-8
        ):
            held_quat = candidate_quat / float(
                np.linalg.norm(candidate_quat)
            )
    except (TypeError, ValueError, OverflowError):
        pass
    held_object_offset_world, offset_reason = _held_object_world_offset(
        visual_hold_evidence,
        held_quat,
    )
    if offset_reason is not None:
        return (
            _failure(
                offset_reason,
                held_object_offset_world=None,
            ),
            env.take_snapshot(),
        )
    motion_quat = {"quat": held_quat} if held_quat is not None else {}

    _perception = os.environ.get("ROBORSI_LIBERO_PERCEPTION", "1") != "0"
    if _perception:
        # High visual drops bounce or miss. Historical successful placements
        # overwhelmingly use 0.06 m; values above 0.08 had no successes.
        z_offset = float(np.clip(z_offset, 0.02, 0.06))
    if _perception and target_name:
        # Tool-calling models often populate optional arrays with [] or
        # [0, 0, 0]. A named target is the authoritative pure-vision input;
        # ignore any simultaneous coordinate placeholder and localize by name.
        pos = None
    if (
        _perception
        and target_name
        and pixel is None
        and _requires_precise_pixel(target_name)
    ):
        return (
            _failure(
                (
                    "relational place target requires an explicit pixel=[u,v]; "
                    "localize the exact compartment/section first"
                ),
            ),
            env.take_snapshot(),
        )
    # PURE-VISION: a hand-computed pos=[x,y,z] is a GUESS — the agent has no reliable
    # pixel->world tool, so its coordinates land the object off the target (the
    # dominant vlm_overclaimed: places beside the plate, claims success). Refuse it
    # and route to object=<description>, which retreats the arm out of the head view,
    # re-localizes the target with a clear view, and drops onto the robust SAM cloud.
    if _perception and pos is not None and pixel is None and not target_name:
        return (
            _failure(
                "pure-vision place takes NO hand-computed pos (you have no reliable "
                "world coordinates). Pass object=\"<the target, e.g. the white plate>\" — "
                "it localizes the target by vision, clears the arm from view, and drops "
                "robustly onto it. (find_pixel is not needed for placing.)"
            ),
            env.take_snapshot(),
        )

    # PURE-VISION gate: in perception mode a bare object=<name> must NOT reach the
    # ground-truth region_box/pos path — localize the container BY VISION and route
    # it through the perceived-cloud drop, so placement uses no ground truth.
    if pixel is None and pos is None and target_name and _perception:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            retreat_from_head_view,
        )

        retreat_reached = retreat_from_head_view(
            env,
            ctrl,
            **motion_quat,
        )
        if not retreat_reached:
            return (
                _failure("could not clear the head-camera view while holding"),
                env.take_snapshot(),
            )
        loc = _clear_localize(state, ctrl, target_name)
        if loc is None:
            return (
                _failure(
                    f"could not perceive container '{target_name}' by vision — "
                    "look() + find_pixel(container)"
                ),
                env.take_snapshot(),
            )
        pixel = [int(loc[0]), int(loc[1])]

    container_geometry = None
    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        drop = np.asarray(pos, dtype=float)                 # explicit release point
    elif isinstance(pixel, (list, tuple)) and len(pixel) == 2:
        # PURE-VISION container drop: SAM cloud at the perceived pixel → robust
        # centroid + rim height. Avoids the fragile single-pixel unproject-z
        # (returns garbage for thin/low baskets) AND uses NO ground-truth.
        release_clearance = z_offset
        pixel_u, pixel_v = int(pixel[0]), int(pixel[1])
        world = env.pixel_to_world(pixel_u, pixel_v)
        drawer_evidence = get_drawer_pull_evidence(env)
        if is_drawer_target(target_name) and drawer_evidence is not None:
            drawer_resolution, drawer_reason = resolve_open_drawer_point(
                drawer_evidence,
                target_name=target_name,
                pixel_world=world,
            )
            if drawer_resolution is None:
                return (
                    _failure(
                        drawer_reason
                        or "drawer placement geometry could not be verified"
                    ),
                    env.take_snapshot(),
                )
            drop = np.asarray(drawer_resolution.point, dtype=float)
            pixel_fallback = "drawer_pull_evidence"
        else:
            from roborsi.embodied.skills.base._lib.libero._perception import (
                object_cloud,
            )

            cloud = object_cloud(env, pixel_u, pixel_v, z_band=0.18)
            container_geometry, geometry_reason = (
                _container_geometry_from_cloud(
                    cloud,
                    pixel=(pixel_u, pixel_v),
                    pixel_world=world,
                )
            )
            if container_geometry is None:
                if _requires_precise_pixel(target_name):
                    return (
                        _failure(
                            geometry_reason
                            or "container geometry could not be verified"
                        ),
                        env.take_snapshot(),
                    )
                drop = _depth_neighborhood_drop(env, pixel_u, pixel_v)
                if drop is None:
                    return (
                        _failure(
                            geometry_reason
                            or "container geometry could not be verified"
                        ),
                        env.take_snapshot(),
                    )
                pixel_fallback = "depth_neighborhood"
            else:
                drop = np.array(
                    [
                        float(container_geometry.center_xy[0]),
                        float(container_geometry.center_xy[1]),
                        float(container_geometry.rim_z),
                    ],
                    dtype=float,
                )
    elif target_name:
        return (
            _failure(
                (
                    "name-only place requires perception localization; provide "
                    "object=<name> in perception mode, or explicit pixel/pos."
                ),
            ),
            env.take_snapshot(),
        )
    else:
        return (
            _failure("give pos=[x,y,z] or object=<name>"),
            env.take_snapshot(),
        )
    if held_object_offset_world is not None:
        drop = drop - held_object_offset_world
    if drop.shape != (3,) or not np.all(np.isfinite(drop)):
        return (
            _failure("resolved container drop point is not finite"),
            env.take_snapshot(),
        )

    if drawer_resolution is not None:
        approach_height = float(release_clearance) + 0.04
    else:
        approach_height = max(
            float(hover),
            float(release_clearance) + 0.04,
        )
    approach = drop + np.array([0.0, 0.0, approach_height])
    approach_reached, _ = ctrl.servo_to(
        approach,
        gripper="close",
        pos_tol=_APPROACH_POS_TOL,
        max_iters=100,
        via_trajopt=True,
        **motion_quat,
    )
    ee, _, _ = ctrl.read_pose()
    approach_fields = _motion_fields("approach", approach, ee)
    if not _motion_within(
        approach,
        ee,
        _APPROACH_POS_TOL,
        _APPROACH_Z_TOL,
    ):
        return (
            _failure(
                "could not reach the container approach pose",
                **approach_fields,
            ),
            env.take_snapshot(),
        )

    gap_approach, state_approach = ctrl.read_gripper_state()
    if state_approach is not GripperState.HELD:
        return (
            _failure(
                "hold lost after container approach",
                hold_check_stage="approach",
                gripper_state_at_gate=state_approach.value,
                gripper_gap_at_gate=round(float(gap_approach), 4),
            ),
            env.take_snapshot(),
        )

    # Descend to a small clearance ABOVE the surface, NOT onto it. A held object
    # hangs BELOW the grip_site, so driving the grip_site down to the perceived
    # surface z rams the object through the table — the arm wedges short and never
    # releases (the 20/20 "wedged short of target" place failure). Stop a few cm
    # high and let the object drop the last stretch onto the target.
    final = drop + np.array([0.0, 0.0, release_clearance])
    final_reached, _ = ctrl.servo_to(
        final,
        gripper="close",
        pos_tol=_FINAL_POS_TOL,
        max_iters=100,
        **motion_quat,
    )
    ee, _, _ = ctrl.read_pose()
    final_fields = _motion_fields("final", final, ee)
    final_correction_attempted = False
    adaptive_clearance_release = bool(
        os.environ.get(
            "ROBORSI_CONTAINER_ADAPTIVE_RELEASE",
            "0",
        )
        == "1"
        and _bounded_container_clearance(final, ee)
        and get_visual_hold(env) is not None
    )
    final_within = _motion_within(
        final,
        ee,
        _FINAL_POS_TOL,
        _FINAL_Z_TOL,
    )
    if (
        not adaptive_clearance_release
        and not final_within
        and _bounded_container_clearance(final, ee)
    ):
        correction_target = bounded_residual_correction_target(
            final,
            ee,
            max_total_error=0.06,
            max_xy_error=_ADAPTIVE_MAX_XY_ERROR,
            max_z_error=_ADAPTIVE_MAX_Z_CLEARANCE,
            max_move=0.06,
        )
        if correction_target is not None:
            _, correction_state = ctrl.read_gripper_state()
            correction_visual = verify_visual_hold(
                env,
                env.take_snapshot().images.get("head_camera"),
                holding=correction_state is GripperState.HELD,
            )
            if (
                correction_state is GripperState.HELD
                and correction_visual.ok
            ):
                final_correction_attempted = True
                ctrl.servo_correction_to(
                    correction_target,
                    gripper="close",
                    pos_tol=0.015,
                    max_iters=100,
                    **motion_quat,
                )
                ee, _, _ = ctrl.read_pose()
                final_fields = _motion_fields("final", final, ee)
                final_within = _motion_within(
                    final,
                    ee,
                    _FINAL_POS_TOL,
                    _FINAL_Z_TOL,
                )
    if not adaptive_clearance_release and not final_within:
        return (
            _failure(
                "could not reach the container release pose",
                final_correction_attempted=final_correction_attempted,
                **final_fields,
            ),
            env.take_snapshot(),
        )

    gap_pre_release, state_pre_release = ctrl.read_gripper_state()
    if state_pre_release is not GripperState.HELD:
        return (
            _failure(
                "hold lost before container release",
                reached=True,
                gripper_state_pre_release=state_pre_release.value,
                gripper_gap_pre_release=round(float(gap_pre_release), 4),
                **final_fields,
            ),
            env.take_snapshot(),
        )
    pre_release_visual = verify_visual_hold(
        env,
        env.take_snapshot().images.get("head_camera"),
        holding=True,
    )
    if not pre_release_visual.ok:
        return (
            _failure(
                "visual hold evidence failed before release",
                reached=True,
                visual_hold_reason=pre_release_visual.reason,
                gripper_state_pre_release=state_pre_release.value,
                gripper_gap_pre_release=round(float(gap_pre_release), 4),
                **final_fields,
            ),
            env.take_snapshot(),
        )

    opened, gap_after, state_after = _open_and_verify(ctrl)
    if not opened:
        return (
            _failure(
                "gripper did not open at the container release pose",
                reached=True,
                gripper_state_pre_release=state_pre_release.value,
                gripper_state_after=state_after.value,
                gripper_gap_after=round(float(gap_after), 4),
                **final_fields,
            ),
            env.take_snapshot(),
        )

    retract_reached, _ = ctrl.servo_to(
        approach,
        gripper="open",
        pos_tol=_APPROACH_POS_TOL,
        max_iters=60,
        **motion_quat,
    )
    if pixel_fallback is not None:
        post_release_containment = _unverified_containment(
            (
                "released via action-grounded drawer placement; "
                "containment unverified"
                if pixel_fallback == "drawer_pull_evidence"
                else (
                    "released via depth-neighborhood fallback; "
                    "containment unverified"
                )
            )
        )
        placed = None
    else:
        post_release_containment = _verify_post_release_containment(
            state,
            geometry=container_geometry,
            object_name=held_object_name,
        )
        placed = bool(post_release_containment.get("placed", False))
    if retract_reached and placed is True:
        reason = "released, visually contained, and retracted"
    elif not retract_reached and placed is True:
        reason = "visually contained after release, but retraction failed"
    elif retract_reached and pixel_fallback == "drawer_pull_evidence":
        reason = (
            "released and retracted using the measured drawer pull; "
            "containment is not visually verified"
        )
    elif pixel_fallback == "drawer_pull_evidence":
        reason = (
            "released using the measured drawer pull, but retraction failed "
            "and containment is not visually verified"
        )
    elif retract_reached and pixel_fallback is not None:
        reason = (
            "released and retracted via depth-neighborhood fallback; "
            "containment is not visually verified"
        )
    elif pixel_fallback is not None:
        reason = (
            "released via depth-neighborhood fallback, but retraction failed "
            "and containment is not visually verified"
        )
    elif retract_reached:
        reason = (
            "released and retracted, but placement was not visually "
            f"confirmed: {post_release_containment.get('reason')}"
        )
    else:
        reason = (
            "object released but retraction failed; placement was not "
            f"visually confirmed: {post_release_containment.get('reason')}"
        )
    held_offset_fields = (
        [
            round(float(value), 4)
            for value in held_object_offset_world
        ]
        if held_object_offset_world is not None
        else None
    )
    expected_object_center = (
        [
            round(float(value), 4)
            for value in final + held_object_offset_world
        ]
        if held_object_offset_world is not None
        else None
    )
    return (
        {
            "ok": bool(retract_reached and placed is True),
            "motion_ok": bool(retract_reached),
            "reached": True,
            "released": True,
            "placed": placed,
            "gripper_opened": True,
            "post_release_visual_containment": post_release_containment,
            "adaptive_clearance_release": adaptive_clearance_release,
            "final_correction_attempted": final_correction_attempted,
            "release_clearance": round(float(release_clearance), 4),
            "reason": reason,
            "pixel_fallback": pixel_fallback,
            "drawer_pixel_translated": (
                drawer_resolution.translated_from_cabinet
                if drawer_resolution is not None
                else None
            ),
            "drawer_source_longitudinal": (
                round(float(drawer_resolution.source_longitudinal), 4)
                if drawer_resolution is not None
                else None
            ),
            "held_object_offset_world": held_offset_fields,
            "expected_object_center_world": expected_object_center,
            "gripper_state_before": state_before.value,
            "gripper_state_pre_release": state_pre_release.value,
            "gripper_state_after": state_after.value,
            "gripper_gap_before": round(float(gap_before), 4),
            "gripper_gap_after": round(float(gap_after), 4),
            **_container_fields(container_geometry),
            **final_fields,
        },
        env.take_snapshot(),
    )
