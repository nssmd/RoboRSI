"""Pure-vision drawer-handle pull for LIBERO."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.drawer_evidence import (
    record_drawer_pull_evidence,
)
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)

_CONTACT_OFFSET = 0.012
_MIN_DRAWER_PULL = 0.18
_MIN_PULL_EVIDENCE = 0.15
_MIN_VISUAL_PULL = 0.03
_MIN_NORMAL_SIGN_ALIGNMENT = 0.05
_HANDLE_SEARCH_RADIUS = 8
_DENSE_FACE_RADIUS = _HANDLE_SEARCH_RADIUS * 2
_MAX_PLANE_FIT_POINTS = 64
_MIN_RELEASE_OPEN_GAP = 0.075


def _bounded_float(
    value: Any,
    default: float,
    low: float,
    high: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if not np.isfinite(result):
        result = default
    return float(np.clip(result, low, high))


def _drawer_pull_distance(value: Any) -> float:
    requested = _bounded_float(
        value,
        _MIN_DRAWER_PULL,
        0.06,
        0.18,
    )
    return max(_MIN_DRAWER_PULL, requested)


def _motion_pull_verified(
    achieved: float,
    gripper_state: GripperState,
) -> bool:
    return bool(
        float(achieved) >= _MIN_PULL_EVIDENCE
        and gripper_state is GripperState.HELD
    )


def _pixel(state: Any, args: dict[str, Any]) -> tuple[int, int] | None:
    value = args.get("pixel")
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
    image = state.env.take_snapshot().images.get("head_camera")
    if image is None:
        return None
    height, width = np.asarray(image).shape[:2]
    if not (0 <= coords[0] < width and 0 <= coords[1] < height):
        return None
    return int(coords[0]), int(coords[1])


def _point(
    env: Any,
    u: int,
    v: int,
    *,
    width: int | None = None,
    height: int | None = None,
) -> np.ndarray | None:
    if width is not None and height is not None:
        if not (0 <= int(u) < int(width) and 0 <= int(v) < int(height)):
            return None
    value = env.pixel_to_world(int(u), int(v))
    if value is None:
        return None
    try:
        point = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return None
    return point


def _fit_face_plane(
    env: Any,
    cloud: np.ndarray,
    handle: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    from itertools import combinations

    cloud = np.asarray(cloud, dtype=float)
    if cloud.ndim != 2 or cloud.shape[1] != 3 or len(cloud) < 6:
        return None
    if len(cloud) > _MAX_PLANE_FIT_POINTS:
        candidate_indices = np.linspace(
            0,
            len(cloud) - 1,
            _MAX_PLANE_FIT_POINTS,
            dtype=int,
        )
        candidate_cloud = cloud[candidate_indices]
    else:
        candidate_cloud = cloud
    best_inliers = None
    best_score = (-1, float("inf"))
    for indices in combinations(range(len(candidate_cloud)), 3):
        first, second, third = candidate_cloud[list(indices)]
        candidate = np.cross(second - first, third - first)
        norm = float(np.linalg.norm(candidate))
        if norm <= 1e-8:
            continue
        candidate = candidate / norm
        if abs(float(candidate[2])) > 0.65:
            continue
        distances = np.abs((cloud - first) @ candidate)
        inliers = distances <= 0.015
        count = int(inliers.sum())
        if count < 5:
            continue
        residual = float(distances[inliers].mean())
        score = (count, -residual)
        if score > best_score:
            best_score = score
            best_inliers = inliers
    if best_inliers is None:
        return None
    plane = cloud[best_inliers]
    centered = plane - np.mean(plane, axis=0)
    covariance = centered.T @ centered / max(1, len(centered) - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)
    if float(eigenvalues[order[1]]) <= 1e-9:
        return None
    if (
        float(eigenvalues[order[0]])
        / float(eigenvalues[order[1]])
        > 0.35
    ):
        return None
    plane_normal = np.asarray(
        eigenvectors[:, order[0]],
        dtype=float,
    )
    if abs(float(plane_normal[2])) > 0.25:
        return None
    normal = plane_normal.copy()
    normal[2] = 0.0
    norm = float(np.linalg.norm(normal))
    if norm <= 1e-8:
        return None
    normal = normal / norm
    base = np.asarray(env.robot_base_pos(), dtype=float)
    toward_robot = base - handle
    toward_robot[2] = 0.0
    toward_robot_norm = float(np.linalg.norm(toward_robot))
    if toward_robot_norm <= 1e-8:
        return None
    alignment = float(
        np.dot(normal, toward_robot / toward_robot_norm)
    )
    if abs(alignment) < _MIN_NORMAL_SIGN_ALIGNMENT:
        return None
    if alignment < 0:
        normal = -normal
        plane_normal = -plane_normal
    return normal, np.mean(plane, axis=0), plane_normal


def _face_plane(
    env: Any,
    u: int,
    v: int,
    handle: np.ndarray,
    image_shape,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    height, width = image_shape[:2]
    points = []
    for radius in (10, 16, 24):
        for du, dv in (
            (-radius, -radius),
            (0, -radius),
            (radius, -radius),
            (-radius, 0),
            (radius, 0),
            (-radius, radius),
            (0, radius),
            (radius, radius),
        ):
            point = _point(
                env,
                u + du,
                v + dv,
                width=width,
                height=height,
            )
            if point is not None:
                points.append(point)
    return _fit_face_plane(
        env,
        np.asarray(points, dtype=float),
        handle,
    )


def _dense_face_plane(
    env: Any,
    u: int,
    v: int,
    handle: np.ndarray,
    image_shape,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    height, width = image_shape[:2]
    points = []
    inner_radius = _HANDLE_SEARCH_RADIUS
    for dv in range(-_DENSE_FACE_RADIUS, _DENSE_FACE_RADIUS + 1):
        for du in range(-_DENSE_FACE_RADIUS, _DENSE_FACE_RADIUS + 1):
            if max(abs(du), abs(dv)) <= inner_radius:
                continue
            point = _point(
                env,
                u + du,
                v + dv,
                width=width,
                height=height,
            )
            if point is not None:
                points.append(point)
    return _fit_face_plane(
        env,
        np.asarray(points, dtype=float),
        handle,
    )


def _face_normal(
    env: Any,
    u: int,
    v: int,
    handle: np.ndarray,
    image_shape,
) -> np.ndarray | None:
    face = _face_plane(env, u, v, handle, image_shape)
    return None if face is None else face[0]


def _refine_drawer_handle_geometry(
    env: Any,
    u: int,
    v: int,
    image_shape,
) -> tuple[tuple[int, int], np.ndarray, np.ndarray] | None:
    height, width = image_shape[:2]
    seed = _point(env, u, v, width=width, height=height)
    if seed is None:
        return None
    face = _face_plane(env, u, v, seed, image_shape)
    if face is None:
        face = _dense_face_plane(
            env,
            u,
            v,
            seed,
            image_shape,
        )
    if face is None:
        return None
    normal, plane_center, plane_normal = face
    candidates = []
    for dv in range(-_HANDLE_SEARCH_RADIUS, _HANDLE_SEARCH_RADIUS + 1):
        for du in range(-_HANDLE_SEARCH_RADIUS, _HANDLE_SEARCH_RADIUS + 1):
            cu, cv = int(u + du), int(v + dv)
            point = _point(
                env,
                cu,
                cv,
                width=width,
                height=height,
            )
            if point is None:
                continue
            protrusion = float(
                np.dot(point - plane_center, plane_normal)
            )
            if 0.004 <= protrusion <= 0.06:
                candidates.append((cu, cv, point, protrusion))
    if len(candidates) < 3:
        return None

    pixels = np.asarray(
        [[row[0], row[1]] for row in candidates],
        dtype=float,
    )
    distances = np.linalg.norm(
        pixels[:, None, :] - pixels[None, :, :],
        axis=2,
    )
    adjacency = distances <= 2.5
    components = []
    remaining = set(range(len(candidates)))
    while remaining:
        pending = [remaining.pop()]
        component = []
        while pending:
            index = pending.pop()
            component.append(index)
            neighbors = [
                int(neighbor)
                for neighbor in np.flatnonzero(adjacency[index])
                if int(neighbor) in remaining
            ]
            for neighbor in neighbors:
                remaining.remove(neighbor)
                pending.append(neighbor)
        if len(component) >= 3:
            components.append(np.asarray(component, dtype=int))
    if not components:
        return None
    component = max(
        components,
        key=lambda indices: (
            -float(
                np.min(
                    np.linalg.norm(
                        pixels[indices] - np.asarray([u, v], dtype=float),
                        axis=1,
                    )
                )
            ),
            len(indices),
            float(
                np.mean(
                    [candidates[int(index)][3] for index in indices]
                )
            ),
        ),
    )
    component_pixels = pixels[component]
    median_pixel = np.median(component_pixels, axis=0)
    medoid = int(
        component[
            np.argmin(
                np.linalg.norm(
                    component_pixels - median_pixel,
                    axis=1,
                )
            )
        ]
    )
    best = int(
        max(
            component,
            key=lambda index: (
                float(candidates[index][3]),
                -float(np.linalg.norm(pixels[index] - pixels[medoid])),
            ),
        )
    )
    refined_uv = (
        int(candidates[best][0]),
        int(candidates[best][1]),
    )
    refined_point = np.asarray(candidates[best][2], dtype=float)
    return refined_uv, refined_point, normal


def _side_entry_quat(normal: np.ndarray) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    approach = -np.asarray(normal, dtype=float)
    approach = approach / np.linalg.norm(approach)
    jaw = np.array([0.0, 0.0, 1.0], dtype=float)
    x_axis = np.cross(jaw, approach)
    x_axis = x_axis / np.linalg.norm(x_axis)
    jaw = np.cross(approach, x_axis)
    matrix = np.column_stack([x_axis, jaw, approach])
    return Rotation.from_matrix(matrix).as_quat()


def _padded_square_crop(
    image: np.ndarray,
    uv: tuple[int, int],
    *,
    radius: int,
) -> tuple[np.ndarray, tuple[int, int]]:
    import cv2

    size = int(radius) * 2
    u, v = int(uv[0]), int(uv[1])
    x0, x1 = max(0, u - radius), min(image.shape[1], u + radius)
    y0, y1 = max(0, v - radius), min(image.shape[0], v + radius)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("invalid crop")
    crop = np.asarray(image[y0:y1, x0:x1])
    left = max(0, radius - u)
    right = max(0, u + radius - image.shape[1])
    top = max(0, radius - v)
    bottom = max(0, v + radius - image.shape[0])
    padded = cv2.copyMakeBorder(
        crop,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_REPLICATE,
    )
    if padded.shape[:2] != (size, size):
        return (
            cv2.resize(
                padded,
                (size, size),
                interpolation=cv2.INTER_CUBIC,
            ),
            (radius, radius),
        )
    return padded, (radius, radius)


def _verify_drawer_handle_pixel(
    state: Any,
    object_name: str,
    rgb: Any,
    uv: tuple[int, int],
) -> bool:
    import cv2

    from roborsi.embodied.agent_loop.vlm_io import (
        _call_vlm_image,
        _parse_json,
    )
    from roborsi.embodied.skills.base._lib.libero._perception import (
        write_image_atomic,
    )

    image = np.asarray(rgb)
    if image.ndim != 3:
        return False
    scale = max(
        2,
        int(os.environ.get("ROBORSI_CANDIDATE_UPSCALE", "3")),
    )
    full = cv2.resize(
        image,
        (image.shape[1] * scale, image.shape[0] * scale),
        interpolation=cv2.INTER_CUBIC,
    )
    full_center = (int(uv[0]) * scale, int(uv[1]) * scale)
    cv2.drawMarker(
        full,
        full_center,
        (255, 40, 40),
        markerType=cv2.MARKER_CROSS,
        markerSize=8 * scale,
        thickness=max(2, scale),
    )
    try:
        crop, crop_center = _padded_square_crop(
            image,
            uv,
            radius=24,
        )
    except ValueError:
        return False
    detail = cv2.resize(
        crop,
        (full.shape[0], full.shape[0]),
        interpolation=cv2.INTER_CUBIC,
    )
    detail_center = (
        int(round(crop_center[0] * detail.shape[1] / crop.shape[1])),
        int(round(crop_center[1] * detail.shape[0] / crop.shape[0])),
    )
    cv2.drawMarker(
        detail,
        detail_center,
        (255, 40, 40),
        markerType=cv2.MARKER_CROSS,
        markerSize=32,
        thickness=4,
    )
    annotated = np.concatenate([full, detail], axis=1)
    workdir = Path(
        getattr(state, "workdir", "/tmp/roborsi-drawer-verify")
    )
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "drawer_handle_candidate.png"
    write_image_atomic(
        path,
        cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR),
    )
    system = (
        "The left panel is the full scene and the right panel is a magnified "
        "local crop. Verify the highlighted cross using both views. "
        "Reject cabinet panels, doors, shelves, loose objects, and handles "
        "on a different drawer layer. "
        "Return one JSON object only: "
        '{"attached_handle": <true|false>, '
        '"correct_drawer_layer": <true|false>, '
        '"pixel_on_handle": <true|false>, '
        '"confidence": <0-1>, "reason": "<short>"}.'
    )
    user = (
        f"Requested target: {object_name}\n"
        "Is the cross on an attached handle of the requested drawer layer?"
    )
    model = os.environ.get(
        "ROBORSI_PERCEPTION_MODEL",
        "anthropic/claude-sonnet-4-6",
    )
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    if not parsed or any(
        parsed.get(field) is not True
        for field in (
            "attached_handle",
            "correct_drawer_layer",
            "pixel_on_handle",
        )
    ):
        return False
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError, OverflowError):
        return False
    minimum_confidence = float(
        os.environ.get(
            "ROBORSI_DRAWER_VERIFY_MIN_CONFIDENCE",
            "0.65",
        )
    )
    return bool(
        np.isfinite(confidence)
        and np.isfinite(minimum_confidence)
        and 0.0 <= minimum_confidence <= 1.0
        and confidence >= minimum_confidence
    )


def _corrected_drawer_handle_pixel(
    state: Any,
    object_name: str,
    rgb: Any,
    uv: tuple[int, int],
) -> tuple[int, int] | None:
    from roborsi.embodied.agent_loop.vlm_io import (
        _call_vlm_image,
        _parse_json,
    )

    image = np.asarray(rgb)
    if image.ndim != 3:
        return None
    path = Path(
        getattr(state, "workdir", "/tmp/roborsi-drawer-verify")
    ) / "drawer_handle_candidate.png"
    if not path.is_file():
        return None
    system = (
        "The left panel is the full 256x256 scene enlarged 3x; the right "
        "panel is a local crop. The red cross marks a proposed drawer-handle "
        "pixel. Return JSON only: "
        '{"pixel_on_requested_handle": <bool>, '
        '"corrected_pixel_original": [u,v] or null, '
        '"confidence": <0-1>, "reason": "<short>"}. '
        "If wrong, corrected_pixel_original must be the center of the "
        "requested handle in the ORIGINAL 256x256 left image. Do not choose "
        "another drawer layer."
    )
    model = os.environ.get(
        "ROBORSI_PERCEPTION_MODEL",
        "anthropic/claude-sonnet-4-6",
    )
    parsed = _parse_json(
        _call_vlm_image(
            model,
            system,
            f"Requested target: {object_name}",
            path,
        )
    )
    if not parsed:
        return None
    value = parsed.get("corrected_pixel_original")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        coords = np.asarray(value, dtype=float)
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        coords.shape != (2,)
        or not np.all(np.isfinite(coords))
        or not np.isfinite(confidence)
        or confidence < 0.55
    ):
        return None
    corrected = (int(round(coords[0])), int(round(coords[1])))
    height, width = image.shape[:2]
    if not (0 <= corrected[0] < width and 0 <= corrected[1] < height):
        return None
    if float(
        np.linalg.norm(
            np.asarray(corrected, dtype=float)
            - np.asarray(uv, dtype=float)
        )
    ) > 48.0:
        return None
    return corrected


def _drawer_layer(object_name: str) -> str | None:
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in str(object_name or "").split()
    }
    if words.intersection({"top", "upper", "highest"}):
        return "top"
    if words.intersection({"middle", "center", "central"}):
        return "middle"
    if words.intersection({"bottom", "lower", "lowest"}):
        return "bottom"
    return None


def _layer_handle_candidates(
    env: Any,
    u: int,
    v: int,
    image_shape,
    layer: str,
) -> list[tuple[tuple[int, int], np.ndarray, np.ndarray]]:
    height, _ = image_shape[:2]
    candidates = _wide_handle_components(
        env,
        int(u),
        int(v),
        image_shape,
    )
    for dv in (-36, -24, -18, -12, 0, 12, 18, 24, 36):
        candidate_v = int(v + dv)
        if not 0 <= candidate_v < int(height):
            continue
        refined = _refine_drawer_handle_geometry(
            env,
            int(u),
            candidate_v,
            image_shape,
        )
        if refined is None:
            continue
        refined_uv = np.asarray(refined[0], dtype=float)
        if any(
            float(
                np.linalg.norm(
                    refined_uv - np.asarray(existing[0], dtype=float)
                )
            )
            <= 10.0
            for existing in candidates
        ):
            continue
        candidates.append(refined)
    if layer == "top":
        return sorted(candidates, key=lambda item: int(item[0][1]))
    if layer == "bottom":
        return sorted(
            candidates,
            key=lambda item: int(item[0][1]),
            reverse=True,
        )
    if layer == "middle" and candidates:
        median_v = float(
            np.median([int(item[0][1]) for item in candidates])
        )
        return sorted(
            candidates,
            key=lambda item: abs(float(item[0][1]) - median_v),
        )
    return []


def _wide_handle_components(
    env: Any,
    u: int,
    v: int,
    image_shape,
) -> list[tuple[tuple[int, int], np.ndarray, np.ndarray]]:
    height, width = image_shape[:2]
    seed = _point(env, u, v, width=width, height=height)
    if seed is None:
        return []
    face = _face_plane(env, u, v, seed, image_shape)
    if face is None:
        face = _dense_face_plane(env, u, v, seed, image_shape)
    if face is None:
        return []
    normal, plane_center, plane_normal = face
    points = {}
    for cv in range(max(0, v - 52), min(height, v + 53)):
        for cu in range(max(0, u - 36), min(width, u + 37)):
            point = _point(
                env,
                cu,
                cv,
                width=width,
                height=height,
            )
            if point is None:
                continue
            protrusion = float(
                np.dot(point - plane_center, plane_normal)
            )
            if 0.004 <= protrusion <= 0.06:
                points[(cu, cv)] = (point, protrusion)
    components = []
    remaining = set(points)
    while remaining:
        pending = [remaining.pop()]
        component = []
        while pending:
            pixel = pending.pop()
            component.append(pixel)
            pu, pv = pixel
            for dv in (-1, 0, 1):
                for du in (-1, 0, 1):
                    neighbor = (pu + du, pv + dv)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
        if 3 <= len(component) <= 400:
            components.append(component)
    out = []
    for component in components:
        pixels = np.asarray(component, dtype=float)
        median = np.median(pixels, axis=0)
        index = int(
            np.argmin(np.linalg.norm(pixels - median, axis=1))
        )
        uv = tuple(int(value) for value in pixels[index])
        out.append((uv, np.asarray(points[uv][0], dtype=float), normal))
    return out


def _resolve_drawer_handle(
    state: Any,
    object_name: str,
    rgb: Any,
    uv: tuple[int, int],
) -> tuple[tuple[int, int], np.ndarray, np.ndarray] | None:
    refined = _refine_drawer_handle_geometry(
        state.env,
        int(uv[0]),
        int(uv[1]),
        np.asarray(rgb).shape,
    )
    last_uv = refined[0] if refined is not None else uv
    reference_normal = refined[2] if refined is not None else None
    layer = _drawer_layer(object_name)
    if layer is not None:
        for candidate in _layer_handle_candidates(
            state.env,
            int(uv[0]),
            int(uv[1]),
            np.asarray(rgb).shape,
            layer,
        ):
            last_uv = candidate[0]
            reference_normal = candidate[2]
            if _verify_drawer_handle_pixel(
                state,
                object_name,
                rgb,
                candidate[0],
            ):
                return candidate
    if refined is not None and _verify_drawer_handle_pixel(
            state,
            object_name,
            rgb,
            refined[0],
    ):
        return refined
    if layer is None:
        return None
    corrected_uv = _corrected_drawer_handle_pixel(
        state,
        object_name,
        rgb,
        last_uv,
    )
    if corrected_uv is None:
        return None
    corrected_point = _point(
        state.env,
        int(corrected_uv[0]),
        int(corrected_uv[1]),
        width=np.asarray(rgb).shape[1],
        height=np.asarray(rgb).shape[0],
    )
    if corrected_point is None:
        return None
    if reference_normal is None:
        reference_normal = _face_normal(
            state.env,
            int(corrected_uv[0]),
            int(corrected_uv[1]),
            corrected_point,
            np.asarray(rgb).shape,
        )
    if reference_normal is not None and _verify_drawer_handle_pixel(
        state,
        object_name,
        rgb,
        corrected_uv,
    ):
        return (
            corrected_uv,
            np.asarray(corrected_point, dtype=float),
            np.asarray(reference_normal, dtype=float),
        )
    return None


def _visual_handle_pull(
    state: Any,
    object_name: str,
    handle_before: np.ndarray,
    normal: np.ndarray,
) -> tuple[float | None, tuple[int, int] | None]:
    from roborsi.embodied.skills.base._lib.libero._perception import (
        localize_precise,
    )

    uv = localize_precise(
        state,
        object_name,
        route="vlm_sam",
    )
    if uv is None:
        return None, None
    after_obs = state.env.take_snapshot()
    after_rgb = after_obs.images.get("head_camera")
    if after_rgb is None:
        return None, (int(uv[0]), int(uv[1]))
    resolved = _resolve_drawer_handle(
        state,
        object_name,
        after_rgb,
        (int(uv[0]), int(uv[1])),
    )
    if resolved is None:
        return None, (int(uv[0]), int(uv[1]))
    verified_uv, handle_after, normal_after = resolved
    if float(
        np.dot(
            np.asarray(normal, dtype=float),
            np.asarray(normal_after, dtype=float),
        )
    ) < 0.9:
        return None, verified_uv
    delta = handle_after - np.asarray(handle_before, dtype=float)
    direction = np.asarray(normal, dtype=float)
    distance = float(np.dot(delta, direction))
    transverse = float(
        np.linalg.norm(delta - distance * direction)
    )
    if distance < 0.0 or distance > 0.25 or transverse > 0.07:
        return None, verified_uv
    return distance, verified_uv


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


def _open(ctrl: LiberoControl) -> tuple[float, GripperState]:
    ctrl.set_gripper(close=False)
    gap, state = ctrl.read_gripper_state()
    if state is not GripperState.OPEN:
        ctrl.set_gripper(close=False)
        gap, state = ctrl.read_gripper_state()
    if (
        state is GripperState.AMBIGUOUS
        and float(gap) >= _MIN_RELEASE_OPEN_GAP
    ):
        state = GripperState.OPEN
    return float(gap), state


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    object_name = str(args.get("object") or "").strip()
    object_words = {
        word.strip(".,;:()[]{}").lower()
        for word in object_name.split()
    }
    if (
        "drawer" not in object_words
        or not object_words.intersection({"handle", "knob", "pull"})
    ):
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": (
                    "object must name the exact drawer handle or knob"
                ),
            },
            env.take_snapshot(),
        )
    uv = _pixel(state, args)
    if uv is None:
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "give a finite in-frame handle pixel=[u,v]",
            },
            env.take_snapshot(),
        )
    ctrl = LiberoControl(env)
    gap_initial, state_initial = ctrl.read_gripper_state()
    if state_initial is GripperState.HELD:
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "refusing drawer pull while the gripper is holding",
                "gripper_state": state_initial.value,
                "gripper_gap": round(float(gap_initial), 4),
            },
            env.take_snapshot(),
        )
    if state_initial is not GripperState.OPEN:
        _, opened_state = _open(ctrl)
        if opened_state is not GripperState.OPEN:
            return (
                {
                    "ok": False,
                    "pulled": False,
                    "reason": "could not open the gripper before approach",
                },
                env.take_snapshot(),
            )
    before_obs = env.take_snapshot()
    before_rgb = before_obs.images.get("head_camera")
    if before_rgb is None:
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "head-camera image unavailable",
            },
            before_obs,
        )
    resolved = _resolve_drawer_handle(
        state,
        object_name,
        before_rgb,
        uv,
    )
    if resolved is None:
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": (
                    "drawer handle pixel was not visually verified"
                ),
            },
            before_obs,
        )
    uv, handle, normal = resolved

    approach_distance = _bounded_float(
        args.get("approach"),
        0.10,
        0.06,
        0.16,
    )
    pull_distance = _drawer_pull_distance(args.get("pull_distance"))
    approach = handle + normal * approach_distance
    contact = handle - normal * _CONTACT_OFFSET
    pull = contact + normal * pull_distance
    quat = _side_entry_quat(normal)

    approach_reached, _ = ctrl.servo_to(
        approach,
        quat=quat,
        gripper="open",
        pos_tol=0.04,
        max_iters=120,
        via_trajopt=True,
    )
    measured, _, _ = ctrl.read_pose()
    if not approach_reached or not _measured_close(
        approach,
        measured,
        0.05,
    ):
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "could not reach the handle approach pose",
            },
            env.take_snapshot(),
        )

    contact_reached, _ = ctrl.servo_to(
        contact,
        quat=quat,
        gripper="open",
        pos_tol=0.03,
        max_iters=100,
    )
    measured, _, _ = ctrl.read_pose()
    if not contact_reached or not _measured_close(
        contact,
        measured,
        0.04,
    ):
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "could not reach the drawer handle",
            },
            env.take_snapshot(),
        )

    ctrl.set_gripper(close=True)
    gap_closed, state_closed = ctrl.read_gripper_state()
    if state_closed is not GripperState.HELD:
        _open(ctrl)
        retract_reached, _ = ctrl.servo_to(
            approach,
            quat=quat,
            gripper="open",
            pos_tol=0.05,
            max_iters=80,
        )
        return (
            {
                "ok": False,
                "pulled": False,
                "reason": "gripper closed without securing the handle",
                "gripper_state": state_closed.value,
                "gripper_gap": round(float(gap_closed), 4),
                "retracted": bool(retract_reached),
            },
            env.take_snapshot(),
        )

    pull_start, _, _ = ctrl.read_pose()
    pull_reached, _ = ctrl.servo_to(
        pull,
        quat=quat,
        gripper="close",
        pos_tol=0.05,
        max_iters=140,
    )
    measured, _, _ = ctrl.read_pose()
    achieved = max(
        0.0,
        float(np.dot(np.asarray(measured) - pull_start, normal)),
    )
    _, state_after_pull = ctrl.read_gripper_state()
    motion_pulled = _motion_pull_verified(achieved, state_after_pull)
    gap_after, state_after = _open(ctrl)
    if state_after is not GripperState.OPEN:
        return (
            {
                "ok": False,
                "pulled": False,
                "pull_reached": bool(pull_reached),
                "retracted": False,
                "pull_distance": round(achieved, 4),
                "reason": "gripper did not open after the drawer pull",
                "gripper_state_after": state_after.value,
                "gripper_gap_after": round(float(gap_after), 4),
            },
            env.take_snapshot(),
        )
    retract_target = (
        np.asarray(measured, dtype=float)
        + normal * 0.06
        + np.array([0.0, 0.0, 0.04])
    )
    retract_reached, _ = ctrl.servo_to(
        retract_target,
        quat=quat,
        gripper="open",
        pos_tol=0.05,
        max_iters=80,
    )
    visual_pull_distance, handle_pixel_after = _visual_handle_pull(
        state,
        object_name,
        handle,
        normal,
    )
    visual_pulled = bool(
        visual_pull_distance is not None
        and visual_pull_distance >= _MIN_VISUAL_PULL
    )
    pulled = bool(motion_pulled)
    drawer_evidence = None
    if pulled and retract_reached:
        drawer_evidence = record_drawer_pull_evidence(
            env,
            target_name=object_name,
            handle_point_before=handle,
            face_normal=normal,
            achieved_pull_distance=achieved,
        )
    return (
        {
            "ok": bool(pulled and retract_reached),
            "pulled": pulled,
            "pull_reached": bool(pull_reached),
            "retracted": bool(retract_reached),
            "pull_distance": round(achieved, 4),
            "requested_pull_distance": round(pull_distance, 4),
            "visual_handle_pull_distance": (
                round(float(visual_pull_distance), 4)
                if visual_pull_distance is not None
                else None
            ),
            "visual_motion_verified": visual_pulled,
            "motion_pull_verified": motion_pulled,
            "drawer_evidence_recorded": drawer_evidence is not None,
            "handle_pixel_after": (
                list(handle_pixel_after)
                if handle_pixel_after is not None
                else None
            ),
            "handle_pixel": [int(uv[0]), int(uv[1])],
            "handle_point": [round(float(value), 4) for value in handle],
            "face_normal": [round(float(value), 4) for value in normal],
            "gripper_state_after": state_after.value,
            "gripper_gap_after": round(float(gap_after), 4),
            "reason": (
                "drawer handle visibly pulled, released, and retracted"
                if pulled and visual_pulled
                else (
                    "drawer handle pull measured, released, and retracted"
                    if motion_pulled
                    else "handle pull did not achieve enough measured motion"
                )
            ),
        },
        env.take_snapshot(),
    )
