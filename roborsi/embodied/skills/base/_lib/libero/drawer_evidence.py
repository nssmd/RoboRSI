"""Action-derived evidence for placing into a drawer opened this episode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_ATTR = "_libero_drawer_pull_evidence"


@dataclass(frozen=True)
class DrawerPullEvidence:
    target_name: str
    layer: str | None
    handle_point_before: tuple[float, float, float]
    face_normal: tuple[float, float, float]
    achieved_pull_distance: float


@dataclass(frozen=True)
class DrawerPlacementResolution:
    point: tuple[float, float, float]
    translated_from_cabinet: bool
    source_longitudinal: float


def drawer_layer(value: Any) -> str | None:
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in str(value or "").split()
    }
    if words.intersection({"top", "upper", "highest"}):
        return "top"
    if words.intersection({"middle", "center", "central"}):
        return "middle"
    if words.intersection({"bottom", "lower", "lowest"}):
        return "bottom"
    return None


def is_drawer_target(value: Any) -> bool:
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in str(value or "").split()
    }
    return "drawer" in words


def get_drawer_pull_evidence(env: Any) -> DrawerPullEvidence | None:
    evidence = getattr(env, _ATTR, None)
    return evidence if isinstance(evidence, DrawerPullEvidence) else None


def record_drawer_pull_evidence(
    env: Any,
    *,
    target_name: str,
    handle_point_before: Any,
    face_normal: Any,
    achieved_pull_distance: Any,
) -> DrawerPullEvidence | None:
    try:
        handle = np.asarray(handle_point_before, dtype=float)
        normal = np.asarray(face_normal, dtype=float)
        distance = float(achieved_pull_distance)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        handle.shape != (3,)
        or normal.shape != (3,)
        or not np.all(np.isfinite(handle))
        or not np.all(np.isfinite(normal))
        or not np.isfinite(distance)
        or distance <= 0.0
    ):
        return None
    normal = normal.copy()
    normal[2] = 0.0
    norm = float(np.linalg.norm(normal))
    if norm <= 0.0:
        return None
    normal /= norm
    evidence = DrawerPullEvidence(
        target_name=str(target_name or "").strip(),
        layer=drawer_layer(target_name),
        handle_point_before=tuple(float(value) for value in handle),
        face_normal=tuple(float(value) for value in normal),
        achieved_pull_distance=distance,
    )
    setattr(env, _ATTR, evidence)
    return evidence


def resolve_open_drawer_point(
    evidence: DrawerPullEvidence | Any,
    *,
    target_name: str,
    pixel_world: Any,
) -> tuple[DrawerPlacementResolution | None, str | None]:
    if evidence is None or not is_drawer_target(target_name):
        return None, "matching drawer pull evidence is unavailable"
    requested_layer = drawer_layer(target_name)
    evidence_layer = getattr(evidence, "layer", None)
    if (
        requested_layer is not None
        and evidence_layer is not None
        and requested_layer != evidence_layer
    ):
        return None, "drawer pull evidence belongs to a different layer"
    try:
        point = np.asarray(pixel_world, dtype=float)
        handle = np.asarray(evidence.handle_point_before, dtype=float)
        normal = np.asarray(evidence.face_normal, dtype=float)
        distance = float(evidence.achieved_pull_distance)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None, "drawer pull evidence is malformed"
    if (
        point.shape != (3,)
        or handle.shape != (3,)
        or normal.shape != (3,)
        or not np.all(np.isfinite(point))
        or not np.all(np.isfinite(handle))
        or not np.all(np.isfinite(normal))
        or not np.isfinite(distance)
        or distance <= 0.0
    ):
        return None, "drawer placement geometry is not finite"
    normal = normal.copy()
    normal[2] = 0.0
    norm = float(np.linalg.norm(normal))
    if norm <= 0.0:
        return None, "drawer pull direction is invalid"
    normal /= norm

    delta = point - handle
    longitudinal = float(np.dot(delta[:2], normal[:2]))
    lateral = float(
        np.linalg.norm(delta[:2] - longitudinal * normal[:2])
    )
    vertical = abs(float(delta[2]))
    if not -distance <= longitudinal <= distance:
        return None, "drawer pixel is outside the measured pull corridor"
    if lateral > distance or vertical > distance:
        return None, "drawer pixel is inconsistent with the pulled cabinet"

    translated = longitudinal < 0.0
    corrected = point + normal * distance if translated else point.copy()
    if translated:
        corrected[:2] = (handle[:2] + corrected[:2]) / 2.0
    corrected_longitudinal = float(
        np.dot((corrected - handle)[:2], normal[:2])
    )
    corrected[:2] = (
        handle[:2] + corrected_longitudinal * normal[:2]
    )
    tolerance = np.finfo(float).eps * max(1.0, abs(distance)) * 16.0
    if not -tolerance <= corrected_longitudinal <= distance + tolerance:
        return None, "corrected drawer point is outside the open drawer"
    return (
        DrawerPlacementResolution(
            point=tuple(float(value) for value in corrected),
            translated_from_cabinet=translated,
            source_longitudinal=longitudinal,
        ),
        None,
    )
