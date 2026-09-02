"""Differential hand-eye calibration — wrist camera on Flexiv.

Intent
------
Build a **2×3 pixel/metre Jacobian** that maps small TCP translations
(dx, dy, dz) to image-plane shifts (du, dv), valid around the current
pose. We do NOT recover full camera intrinsics or the 6-DoF wrist→camera
transform — just what's needed for short-horizon visual servoing.

Method
------
1. Snapshot at base pose P0.
2. For each axis a ∈ {x, y, z} and sign s ∈ {+, -}, move TCP by s·δ_a,
   snap, then return to P0. Measure the 2D image shift between that
   snapshot and the base via ``cv2.phaseCorrelate`` on a central ROI of
   the textured workspace.
3. Six shift measurements ⇒ 2×3 Jacobian via ordinary least squares.
4. Persist to ``~/.roborsi/workspace/embodied/calibration/<arm>_<camera>.json``.

The Jacobian is valid at the pose it was captured at; large Z changes
invalidate it (pixel scale depends on object distance). Re-calibrate if
you move much.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class CalibrationResult:
    """2×3 Jacobian ∂[u,v]/∂[x,y,z] (pixels per metre) + metadata."""

    jacobian: list[list[float]]   # shape (2,3): rows [∂u/∂xyz, ∂v/∂xyz]
    anchor_pose: list[float]      # TCP pose at calibration
    delta_m: float                # step size used
    residuals_px: list[float]     # fit residuals (one per measurement)
    roi: list[int]                # [u0, v0, w, h] used for phaseCorrelate
    captured_at: str              # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "jacobian": self.jacobian,
            "anchor_pose": self.anchor_pose,
            "delta_m": self.delta_m,
            "residuals_px": self.residuals_px,
            "roi": self.roi,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CalibrationResult":
        return cls(
            jacobian=d["jacobian"],
            anchor_pose=d["anchor_pose"],
            delta_m=d["delta_m"],
            residuals_px=d.get("residuals_px", []),
            roi=d.get("roi", [0, 0, 0, 0]),
            captured_at=d["captured_at"],
        )


def calibration_path(arm_alias: str, camera_alias: str, home: Path | None = None) -> Path:
    from roborsi.embodied.embodiment.manifest.helpers import get_roborsi_home
    root = home or get_roborsi_home()
    return root / "workspace" / "embodied" / "calibration" / f"{arm_alias}_{camera_alias}.json"


def measure_shift(base_gray: np.ndarray, moved_gray: np.ndarray, roi: tuple[int, int, int, int]) -> tuple[float, float]:
    """Return (du, dv) pixel shift from base → moved via phase correlation on ROI.

    Convention: ``moved_feature_pixel - base_feature_pixel``. Positive du
    means scene features moved right in the image; positive dv means down.
    Synthetic test (see unit test): a rightward 15-px shift yields (+15, 0).
    """
    import cv2
    u0, v0, w, h = roi
    a = base_gray[v0:v0 + h, u0:u0 + w].astype(np.float32)
    b = moved_gray[v0:v0 + h, u0:u0 + w].astype(np.float32)
    shift, _response = cv2.phaseCorrelate(a, b)
    return float(shift[0]), float(shift[1])


def fit_jacobian(
    deltas_m: list[tuple[float, float, float]],
    shifts_px: list[tuple[float, float]],
) -> tuple[np.ndarray, list[float]]:
    """Least-squares fit J @ [dx, dy, dz]^T = [du, dv]^T across all samples."""
    X = np.array(deltas_m, dtype=np.float64)
    Y = np.array(shifts_px, dtype=np.float64)
    # Solve J^T via lstsq: X @ J^T = Y  →  J^T = lstsq(X, Y)
    jt, residuals, _, _ = np.linalg.lstsq(X, Y, rcond=None)
    J = jt.T  # shape (2, 3)
    preds = X @ jt
    resid_px = np.linalg.norm(Y - preds, axis=1).tolist()
    return J, resid_px
