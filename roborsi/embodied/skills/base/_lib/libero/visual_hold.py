"""Pure-vision evidence that the active LIBERO grip removed an object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_ATTR = "_libero_visual_hold_evidence"
_PENDING_ATTR = "_libero_pending_visual_hold_evidence"
_PATCH_RADIUS = 12
_MIN_SOURCE_MAD = 3.0
_MIN_DEPTH_CLEARANCE = 0.01


@dataclass(frozen=True)
class VisualHoldEvidence:
    object_name: str
    source_pixel: tuple[int, int]
    source_before: np.ndarray
    source_after: np.ndarray
    source_mad: float
    identity_verified: bool = False
    object_offset_local: tuple[float, float, float] | None = None
    release_clearance_hint: float | None = None
    pickup_reference: Any | None = None


@dataclass(frozen=True)
class PendingVisualHoldEvidence:
    object_name: str
    source_pixel: tuple[int, int]
    source_before: np.ndarray
    source_before_depth: np.ndarray
    source_patch_center: tuple[int, int]
    identity_verified: bool = False
    object_offset_local: tuple[float, float, float] | None = None
    release_clearance_hint: float | None = None
    pickup_reference: Any | None = None


@dataclass(frozen=True)
class VisualHoldVerification:
    ok: bool
    reason: str
    object_name: str | None = None
    identity_verified: bool = False
    current_source_mad: float | None = None
    current_to_after_mad: float | None = None
    frame_token: str | None = None
    instance_mask: np.ndarray | None = None
    robot_mask: np.ndarray | None = None
    association_metrics: dict[str, float] | None = None


def _crop_patch(image: Any, pixel: tuple[int, int]) -> np.ndarray | None:
    if image is None:
        return None
    array = np.asarray(image)
    if array.ndim != 3:
        return None
    u, v = int(pixel[0]), int(pixel[1])
    height, width = array.shape[:2]
    x0 = max(0, u - _PATCH_RADIUS)
    x1 = min(width, u + _PATCH_RADIUS)
    y0 = max(0, v - _PATCH_RADIUS)
    y1 = min(height, v + _PATCH_RADIUS)
    if x0 >= x1 or y0 >= y1:
        return None
    return np.asarray(array[y0:y1, x0:x1], dtype=np.float32).copy()


def _patch_mad(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.shape != right.shape or left.size == 0:
        return None
    value = float(np.abs(left - right).mean())
    return value if np.isfinite(value) else None


def _crop_depth(depth: Any, pixel: tuple[int, int]) -> np.ndarray | None:
    if depth is None:
        return None
    array = np.asarray(depth)
    if array.ndim == 3 and array.shape[2] >= 1:
        array = array[..., 0]
    if array.ndim != 2:
        return None
    u, v = int(pixel[0]), int(pixel[1])
    height, width = array.shape[:2]
    x0 = max(0, u - _PATCH_RADIUS)
    x1 = min(width, u + _PATCH_RADIUS)
    y0 = max(0, v - _PATCH_RADIUS)
    y1 = min(height, v + _PATCH_RADIUS)
    if x0 >= x1 or y0 >= y1:
        return None
    return np.asarray(array[y0:y1, x0:x1], dtype=np.float32).copy()


def capture_depth_frame(env: Any) -> np.ndarray | None:
    try:
        depth = env.depth_map("agentview")
    except TypeError:
        try:
            depth = env.depth_map()
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    if depth is None:
        return None
    array = np.asarray(depth)
    if array.ndim not in {2, 3}:
        return None
    return array.copy()


def _finite_depth_median(depth: np.ndarray) -> float | None:
    values = np.asarray(depth, dtype=float)
    finite = values[np.isfinite(values) & (values > 0.0)]
    if not len(finite):
        return None
    value = float(np.median(finite))
    return value if np.isfinite(value) else None


def clear_visual_hold(env: Any) -> None:
    setattr(env, _ATTR, None)
    setattr(env, _PENDING_ATTR, None)
    setattr(env, "_libero_visual_hold_association", None)


def get_visual_hold(env: Any) -> VisualHoldEvidence | None:
    evidence = getattr(env, _ATTR, None)
    return evidence if isinstance(evidence, VisualHoldEvidence) else None


def get_pending_visual_hold(env: Any) -> PendingVisualHoldEvidence | None:
    evidence = getattr(env, _PENDING_ATTR, None)
    return (
        evidence
        if isinstance(evidence, PendingVisualHoldEvidence)
        else None
    )


def record_pending_visual_hold(
    env: Any,
    *,
    object_name: str,
    source_pixel: tuple[int, int],
    before_rgb: Any,
    before_depth: Any,
    identity_verified: bool = False,
    object_offset_local: tuple[float, float, float] | None = None,
    release_clearance_hint: float | None = None,
    pickup_reference: Any | None = None,
) -> PendingVisualHoldEvidence | None:
    clear_visual_hold(env)
    if identity_verified and pickup_reference is None:
        return None
    before = _crop_patch(before_rgb, source_pixel)
    depth = _crop_depth(before_depth, source_pixel)
    if before is None or depth is None:
        return None
    if _finite_depth_median(depth) is None:
        return None
    evidence = PendingVisualHoldEvidence(
        object_name=str(object_name or "").strip(),
        source_pixel=(int(source_pixel[0]), int(source_pixel[1])),
        source_before=before,
        source_before_depth=depth,
        source_patch_center=(
            min(_PATCH_RADIUS, int(source_pixel[0])),
            min(_PATCH_RADIUS, int(source_pixel[1])),
        ),
        identity_verified=bool(identity_verified),
        object_offset_local=object_offset_local,
        release_clearance_hint=release_clearance_hint,
        pickup_reference=pickup_reference,
    )
    setattr(env, _PENDING_ATTR, evidence)
    return evidence


def record_visual_hold(
    env: Any,
    *,
    object_name: str,
    source_pixel: tuple[int, int],
    before_rgb: Any,
    after_rgb: Any,
    identity_verified: bool = False,
    object_offset_local: tuple[float, float, float] | None = None,
    release_clearance_hint: float | None = None,
    pickup_reference: Any | None = None,
) -> VisualHoldEvidence | None:
    clear_visual_hold(env)
    if identity_verified and pickup_reference is None:
        return None
    before = _crop_patch(before_rgb, source_pixel)
    after = _crop_patch(after_rgb, source_pixel)
    if before is None or after is None:
        return None
    source_mad = _patch_mad(before, after)
    if source_mad is None or source_mad <= _MIN_SOURCE_MAD:
        return None
    evidence = VisualHoldEvidence(
        object_name=str(object_name or "").strip(),
        source_pixel=(int(source_pixel[0]), int(source_pixel[1])),
        source_before=before,
        source_after=after,
        source_mad=source_mad,
        identity_verified=bool(identity_verified),
        object_offset_local=object_offset_local,
        release_clearance_hint=release_clearance_hint,
        pickup_reference=pickup_reference,
    )
    setattr(env, _ATTR, evidence)
    return evidence


def verify_visual_hold(
    env: Any,
    current_rgb: Any,
    *,
    holding: bool | None = None,
    frame: Any | None = None,
) -> VisualHoldVerification:
    evidence = get_visual_hold(env)
    if evidence is None:
        pending = get_pending_visual_hold(env)
        if pending is None:
            return VisualHoldVerification(
                ok=False,
                reason="missing_visual_hold_evidence",
            )
        if holding is not True:
            return VisualHoldVerification(
                ok=False,
                reason="pending_hold_not_confirmed",
                object_name=pending.object_name,
            )
        current = _crop_patch(current_rgb, pending.source_pixel)
        if current is None:
            return VisualHoldVerification(
                ok=False,
                reason="visual_hold_frame_unavailable",
                object_name=pending.object_name,
            )
        current_source_mad = _patch_mad(
            pending.source_before,
            current,
        )
        if (
            current_source_mad is None
            or current_source_mad <= _MIN_SOURCE_MAD
        ):
            return VisualHoldVerification(
                ok=False,
                reason="pending_source_patch_unchanged",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        current_depth = _crop_depth(
            capture_depth_frame(env),
            pending.source_pixel,
        )
        if current_depth is None:
            return VisualHoldVerification(
                ok=False,
                reason="pending_source_depth_unavailable",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        if current_depth.shape != pending.source_before_depth.shape:
            return VisualHoldVerification(
                ok=False,
                reason="pending_source_depth_unavailable",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        cx, cy = pending.source_patch_center
        yy, xx = np.ogrid[
            : current_depth.shape[0],
            : current_depth.shape[1],
        ]
        center_mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= 4 ** 2
        before_values = np.asarray(
            pending.source_before_depth,
            dtype=float,
        )
        after_values = np.asarray(current_depth, dtype=float)
        paired = (
            center_mask
            & np.isfinite(before_values)
            & np.isfinite(after_values)
            & (before_values > 0.0)
            & (after_values > 0.0)
        )
        if int(paired.sum()) < 9:
            return VisualHoldVerification(
                ok=False,
                reason="pending_source_depth_unavailable",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        depth_delta = after_values[paired] - before_values[paired]
        cleared_fraction = float(
            np.mean(depth_delta >= _MIN_DEPTH_CLEARANCE)
        )
        if (
            float(np.median(depth_delta)) < _MIN_DEPTH_CLEARANCE
            or cleared_fraction < 0.60
        ):
            return VisualHoldVerification(
                ok=False,
                reason="pending_source_depth_not_cleared",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        if not pending.identity_verified or pending.pickup_reference is None:
            return VisualHoldVerification(
                ok=False,
                reason="pending_pickup_reference_missing",
                object_name=pending.object_name,
                current_source_mad=current_source_mad,
            )
        from roborsi.embodied.skills.base._lib.libero.instance_hold import (
            associate_pickup_reference,
        )

        association = associate_pickup_reference(
            env,
            pending.pickup_reference,
            holding=True,
            frame=frame,
        )
        if not association.ok:
            setattr(env, "_libero_visual_hold_association", association)
            return VisualHoldVerification(
                ok=False,
                reason=f"pending_instance_{association.reason}",
                object_name=pending.object_name,
                identity_verified=False,
                current_source_mad=current_source_mad,
                frame_token=association.frame_token,
                instance_mask=association.mask,
                robot_mask=association.robot_mask,
                association_metrics=association.metrics,
            )
        promoted = VisualHoldEvidence(
            object_name=pending.object_name,
            source_pixel=pending.source_pixel,
            source_before=pending.source_before,
            source_after=current,
            source_mad=current_source_mad,
            identity_verified=True,
            object_offset_local=pending.object_offset_local,
            release_clearance_hint=pending.release_clearance_hint,
            pickup_reference=pending.pickup_reference,
        )
        setattr(env, _ATTR, promoted)
        setattr(env, _PENDING_ATTR, None)
        setattr(env, "_libero_visual_hold_association", association)
        return VisualHoldVerification(
            ok=True,
            reason="pending_visual_hold_promoted_and_associated",
            object_name=pending.object_name,
            identity_verified=True,
            current_source_mad=current_source_mad,
            current_to_after_mad=0.0,
            frame_token=association.frame_token,
            instance_mask=association.mask,
            robot_mask=association.robot_mask,
            association_metrics=association.metrics,
        )
    if holding is not True:
        return VisualHoldVerification(
            ok=False,
            reason="hold_not_confirmed",
            object_name=evidence.object_name,
        )
    if not evidence.identity_verified or evidence.pickup_reference is None:
        return VisualHoldVerification(
            ok=False,
            reason="authenticated_pickup_reference_missing",
            object_name=evidence.object_name,
            identity_verified=False,
        )
    # source_mad is immutable historical removal evidence. Requiring the old
    # pickup pixel to remain visually empty after transport confuses unrelated
    # reoccupation with loss of the held instance. Current continuity is instead
    # authenticated below from the pickup reference and measured robot pose.
    if evidence.source_mad <= _MIN_SOURCE_MAD:
        return VisualHoldVerification(
            ok=False,
            reason="historical_source_clearance_invalid",
            object_name=evidence.object_name,
        )
    from roborsi.embodied.skills.base._lib.libero.instance_hold import (
        associate_pickup_reference,
    )

    association = associate_pickup_reference(
        env,
        evidence.pickup_reference,
        holding=True,
        frame=frame,
    )
    if not association.ok:
        setattr(env, "_libero_visual_hold_association", association)
        return VisualHoldVerification(
            ok=False,
            reason=f"instance_{association.reason}",
            object_name=evidence.object_name,
            identity_verified=False,
            current_source_mad=evidence.source_mad,
            frame_token=association.frame_token,
            instance_mask=association.mask,
            robot_mask=association.robot_mask,
            association_metrics=association.metrics,
        )
    setattr(env, "_libero_visual_hold_association", association)
    return VisualHoldVerification(
        ok=True,
        reason="historical_source_cleared_and_instance_associated",
        object_name=evidence.object_name,
        identity_verified=True,
        current_source_mad=evidence.source_mad,
        current_to_after_mad=0.0,
        frame_token=association.frame_token,
        instance_mask=association.mask,
        robot_mask=association.robot_mask,
        association_metrics=association.metrics,
    )
