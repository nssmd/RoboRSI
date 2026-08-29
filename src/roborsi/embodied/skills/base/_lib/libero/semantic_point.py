"""Frame-bound provenance for semantic point localizations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

_LATEST_ATTR = "_libero_semantic_point_evidence"
_INDEX_ATTR = "_libero_semantic_point_evidence_by_object"
_MAX_ENTRIES = 8
_MAX_FRAME_MAD = 1.0
_MAX_PIXEL_DISTANCE = 4.0


@dataclass(frozen=True)
class SemanticPointEvidence:
    object_name: str
    pixel: tuple[int, int]
    frame: np.ndarray
    source: str


def _normalized_name(value: Any) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return " ".join(token for token in tokens if token not in {"a", "an", "the"})


def record_semantic_point(
    env: Any,
    *,
    object_name: str,
    pixel: tuple[int, int],
    frame: Any,
    source: str,
) -> SemanticPointEvidence | None:
    name = str(object_name or "").strip()
    normalized = _normalized_name(name)
    image = np.asarray(frame)
    if not normalized or image.ndim != 3:
        return None
    try:
        u, v = int(pixel[0]), int(pixel[1])
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    height, width = image.shape[:2]
    if not (0 <= u < width and 0 <= v < height):
        return None
    evidence = SemanticPointEvidence(
        object_name=name,
        pixel=(u, v),
        frame=image.copy(),
        source=str(source or "").strip(),
    )
    index = getattr(env, _INDEX_ATTR, None)
    if not isinstance(index, dict):
        index = {}
    else:
        index = dict(index)
    index.pop(normalized, None)
    index[normalized] = evidence
    while len(index) > _MAX_ENTRIES:
        index.pop(next(iter(index)))
    setattr(env, _INDEX_ATTR, index)
    setattr(env, _LATEST_ATTR, evidence)
    return evidence


def matching_semantic_point(
    env: Any,
    *,
    object_name: str,
    pixel: tuple[int, int],
    current_frame: Any,
) -> SemanticPointEvidence | None:
    index = getattr(env, _INDEX_ATTR, None)
    if not isinstance(index, dict):
        return None
    evidence = index.get(_normalized_name(object_name))
    if not isinstance(evidence, SemanticPointEvidence):
        return None
    if evidence.source != "vlm->sam":
        return None
    try:
        candidate = np.asarray(pixel, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        candidate.shape != (2,)
        or not np.all(np.isfinite(candidate))
        or float(
            np.linalg.norm(candidate - np.asarray(evidence.pixel, dtype=float))
        )
        > _MAX_PIXEL_DISTANCE
    ):
        return None
    current = np.asarray(current_frame)
    if current.shape != evidence.frame.shape or current.ndim != 3:
        return None
    frame_mad = float(
        np.abs(current.astype(np.float32) - evidence.frame.astype(np.float32)).mean()
    )
    if not np.isfinite(frame_mad) or frame_mad > _MAX_FRAME_MAD:
        return None
    return evidence
