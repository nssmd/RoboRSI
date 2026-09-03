from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class GripperState(str, Enum):
    OPEN = "open"
    CLOSED_EMPTY = "closed_empty"
    HELD = "held"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class GripperCalibration:
    open_gap: float
    closed_gap: float
    settled_sigma: float

    @property
    def tolerance(self) -> float:
        return max(6.0 * self.settled_sigma, np.finfo(float).eps)

    @classmethod
    def from_joint_ranges(
        cls,
        left_range: tuple[float, float],
        right_range: tuple[float, float],
        *,
        settled_sigma: float | None = None,
    ) -> "GripperCalibration":
        open_gap = float(left_range[1]) - float(right_range[0])
        closed_gap = float(left_range[0]) - float(right_range[1])
        if open_gap < closed_gap:
            open_gap, closed_gap = closed_gap, open_gap
        span = max(open_gap - closed_gap, np.finfo(float).eps)
        model_precision = np.finfo(float).eps * max(abs(open_gap), abs(closed_gap), 1.0)
        derived_sigma = max(span * 1e-3, 64.0 * model_precision)
        sigma = derived_sigma if settled_sigma is None else float(settled_sigma)
        return cls(
            open_gap=open_gap,
            closed_gap=closed_gap,
            settled_sigma=max(sigma, np.finfo(float).eps),
        )

    def classify(
        self,
        gap: float,
        *,
        last_command: str | None,
        tolerance: float | None = None,
    ) -> GripperState:
        tol = self.tolerance if tolerance is None else max(float(tolerance), np.finfo(float).eps)
        span = max(self.open_gap - self.closed_gap, np.finfo(float).eps)
        endpoint_band = max(tol, 0.02 * span)
        if abs(gap - self.open_gap) <= endpoint_band:
            return GripperState.OPEN
        if (
            last_command == "open"
            and gap >= self.closed_gap + 0.85 * span
        ):
            return GripperState.OPEN
        if abs(gap - self.closed_gap) <= endpoint_band:
            return GripperState.CLOSED_EMPTY
        if (
            last_command == "close"
            and self.closed_gap + endpoint_band < gap < self.open_gap - endpoint_band
        ):
            return GripperState.HELD
        return GripperState.AMBIGUOUS


@dataclass
class _RollingVariance:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / float(self.count)
        delta2 = value - self.mean
        self.m2 += delta * delta2

    @property
    def variance(self) -> float | None:
        if self.count < 2:
            return None
        return max(self.m2 / float(self.count - 1), 0.0)

    @property
    def sigma(self) -> float | None:
        var = self.variance
        if var is None:
            return None
        return float(np.sqrt(var))


@dataclass
class GripperClassifier:
    calibration: GripperCalibration
    hold_latched: bool = False
    close_stalled_open: bool = False
    open_stats: _RollingVariance = field(default_factory=_RollingVariance)
    closed_stats: _RollingVariance = field(default_factory=_RollingVariance)

    @property
    def open_samples(self) -> int:
        return int(self.open_stats.count)

    @property
    def closed_samples(self) -> int:
        return int(self.closed_stats.count)

    @property
    def endpoint_tolerance(self) -> float:
        sigmas = [self.calibration.settled_sigma]
        if self.open_stats.sigma is not None:
            sigmas.append(float(self.open_stats.sigma))
        if self.closed_stats.sigma is not None:
            sigmas.append(float(self.closed_stats.sigma))
        return max(6.0 * max(sigmas), np.finfo(float).eps)

    def _close_motion_threshold(self) -> float:
        span = max(self.calibration.open_gap - self.calibration.closed_gap, np.finfo(float).eps)
        return max(4.0 * self.endpoint_tolerance, 0.03 * span)

    def confirm_close(self, *, pre_gap: float, post_gap: float) -> None:
        moved = float(pre_gap - post_gap)
        tol = self.endpoint_tolerance
        in_hold_band = (
            self.calibration.closed_gap + tol
            < float(post_gap)
            < self.calibration.open_gap - tol
        )
        moved_enough = moved >= self._close_motion_threshold()
        at_open = abs(float(post_gap) - self.calibration.open_gap) <= tol
        at_closed = abs(float(post_gap) - self.calibration.closed_gap) <= tol
        stalled_near_open = (
            moved < self._close_motion_threshold()
            and float(post_gap) >= self.calibration.open_gap - 3.0 * tol
        )

        if moved_enough and in_hold_band:
            self.hold_latched = True
            self.close_stalled_open = False
            return
        if at_open:
            self.hold_latched = False
            self.close_stalled_open = True
            return
        if stalled_near_open:
            self.hold_latched = False
            self.close_stalled_open = True
            return
        if at_closed:
            self.hold_latched = False
            self.close_stalled_open = False
            return
        if self.hold_latched and moved < self._close_motion_threshold():
            # A repeated explicit close with no additional closure movement
            # does not refute an already confirmed hold.
            self.close_stalled_open = False
            return
        self.hold_latched = False
        self.close_stalled_open = False

    def on_keep_close(self, *, gap: float) -> None:
        # Holding close commands should not erase a previously confirmed hold.
        if abs(float(gap) - self.calibration.closed_gap) <= self.endpoint_tolerance:
            self.hold_latched = False
            self.close_stalled_open = False
            return
        if abs(float(gap) - self.calibration.open_gap) <= self.endpoint_tolerance:
            self.hold_latched = False
            self.close_stalled_open = True

    def confirm_open(self, *, gap: float) -> None:
        _ = gap
        self.hold_latched = False
        self.close_stalled_open = False

    def on_keep_open(self, *, gap: float) -> None:
        if abs(float(gap) - self.calibration.open_gap) <= self.endpoint_tolerance:
            self.hold_latched = False
            self.close_stalled_open = False

    def _observe_endpoint(self, gap: float, base_state: GripperState) -> None:
        if base_state is GripperState.OPEN:
            self.open_stats.update(float(gap))
        elif base_state is GripperState.CLOSED_EMPTY:
            self.closed_stats.update(float(gap))

    def classify(self, gap: float, *, last_command: str | None) -> GripperState:
        tol = self.endpoint_tolerance
        base = self.calibration.classify(gap, last_command=last_command, tolerance=tol)
        self._observe_endpoint(gap, base)
        if base is GripperState.OPEN:
            self.hold_latched = False
            self.close_stalled_open = False
            return GripperState.OPEN
        if base is GripperState.CLOSED_EMPTY:
            self.hold_latched = False
            self.close_stalled_open = False
            return GripperState.CLOSED_EMPTY
        if last_command == "close":
            if self.close_stalled_open:
                return GripperState.OPEN
            if self.hold_latched:
                return GripperState.HELD
            if base is GripperState.HELD:
                return GripperState.AMBIGUOUS
        return base
