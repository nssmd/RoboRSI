"""place_held_at_target_servo — closed-loop precise placement of the held object
at a target POSE (position + optional orientation), base/libero.

For tight-tolerance placement (onto a pad / plate / stand / rack) where both
where AND how the object lands matter. Unlike place_object_in (drops from above
a container region) this servos the end-effector to an exact pose with a tight
position tolerance and, if a quat is given, aligns orientation before releasing.
Perception-resolved target pose; single-arm OSC servo.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import (
    LiberoControl,
    bounded_residual_correction_target,
)
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState

_APPROACH_POS_TOL = 0.02
_APPROACH_Z_TOL = 0.015
_MAX_FINAL_Z_ERROR = 0.008


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if not np.isfinite(result):
        result = default
    return float(np.clip(result, low, high))


def _valid_point(value: Any) -> np.ndarray | None:
    try:
        point = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        return None
    return point


def _motion_fields(prefix: str, target, measured) -> dict[str, Any]:
    point = _valid_point(measured)
    position_error = (
        float(np.linalg.norm(point - target))
        if point is not None
        else float("inf")
    )
    z_error = (
        abs(float(point[2] - target[2]))
        if point is not None
        else float("inf")
    )
    return {
        f"{prefix}_position_error": (
            round(position_error, 4) if np.isfinite(position_error) else None
        ),
        f"{prefix}_z_error": round(z_error, 4) if np.isfinite(z_error) else None,
        "ee_pos": (
            [round(float(v), 4) for v in point]
            if point is not None
            else None
        ),
    }


def _motion_within(target, measured, pos_tol: float, z_tol: float) -> bool:
    point = _valid_point(measured)
    return bool(
        point is not None
        and float(np.linalg.norm(point - target)) <= pos_tol
        and abs(float(point[2] - target[2])) <= z_tol
    )


def _rotation_within(measured, target, tol: float) -> bool:
    if target is None:
        return True
    current = np.asarray(measured, dtype=float)
    desired = np.asarray(target, dtype=float)
    if (
        current.shape != (4,)
        or desired.shape != (4,)
        or not np.all(np.isfinite(current))
        or not np.all(np.isfinite(desired))
    ):
        return False
    current_norm = float(np.linalg.norm(current))
    desired_norm = float(np.linalg.norm(desired))
    if current_norm <= 0.0 or desired_norm <= 0.0:
        return False
    dot = float(
        np.clip(
            abs(
                np.dot(
                    current / current_norm,
                    desired / desired_norm,
                )
            ),
            0.0,
            1.0,
        )
    )
    return 2.0 * float(np.arccos(dot)) <= tol


def _failure(reason: str, **fields) -> dict[str, Any]:
    return {
        "ok": False,
        "reached": False,
        "released": False,
        "gripper_opened": False,
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
    pos = args.get("pos")
    name = str(args.get("object") or "").strip()
    quat = args.get("quat")
    hover = _bounded_float(args.get("hover", 0.12), 0.12, 0.05, 0.30)
    z_offset = _bounded_float(args.get("z_offset", 0.0), 0.0, -0.10, 0.20)
    pos_tol = _bounded_float(args.get("pos_tol", 0.006), 0.006, 0.002, 0.05)

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

    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        target = _valid_point(pos)
    elif name:
        # PURE VISION (no ground truth): RETREAT the held object out of the
        # agentview head view (lift high AND slide toward the robot base so the
        # arm stops occluding the target — a straight-up lift leaves it hanging
        # over the workspace), localize the target with SAM3, and take the
        # perceived cloud centroid + surface.
        from roborsi.embodied.skills.base._lib.libero._perception import (
            _place_fix_on,
            localize_precise,
            object_cloud,
            retreat_from_head_view,
        )

        if _place_fix_on():
            retreat_reached = retreat_from_head_view(env, ctrl)
        else:
            e0, _, _ = ctrl.read_pose()
            retreat_reached, _ = ctrl.servo_to(
                [float(e0[0]), float(e0[1]), float(e0[2]) + 0.18],
                gripper="close",
                max_iters=50,
            )
        if not retreat_reached:
            return (
                _failure(
                    "could not clear the head-camera view while holding",
                    gripper_state_before=state_before.value,
                ),
                env.take_snapshot(),
            )
        loc = localize_precise(state, name)
        if loc is None or (abs(int(loc[0]) - 128) <= 2 and abs(int(loc[1]) - 128) <= 2):
            return (
                _failure(
                    f"could not perceive target '{name}' by vision — look() + find_pixel",
                ),
                env.take_snapshot(),
            )
        cloud = object_cloud(env, int(loc[0]), int(loc[1]), z_band=0.18)
        if cloud is None or len(cloud) < 20:
            return (
                {
                    "ok": False,
                    "reason": f"could not perceive target '{name}' cloud — re-find_pixel",
                },
                env.take_snapshot(),
            )
        target = _valid_point(
            [
                float(np.median(cloud[:, 0])),
                float(np.median(cloud[:, 1])),
                float(np.percentile(cloud[:, 2], 85)),
            ]
        )
    else:
        target = None
    if target is None:
        return (
            _failure("give a finite pos=[x,y,z] or object=<name>"),
            env.take_snapshot(),
        )
    target = target + np.array([0.0, 0.0, z_offset])

    q = None
    if quat is not None:
        try:
            candidate = np.asarray(quat, dtype=float)
        except (TypeError, ValueError, OverflowError):
            candidate = np.array([])
        if candidate.size == 0:
            candidate = None
        elif candidate.shape != (4,) or not np.all(np.isfinite(candidate)):
            return (
                _failure("quat must be a finite [qx,qy,qz,qw]"),
                env.take_snapshot(),
            )
        q = candidate

    approach = target + np.array([0.0, 0.0, hover])
    approach_reached, _ = ctrl.servo_to(
        approach,
        quat=q,
        gripper="close",
        pos_tol=_APPROACH_POS_TOL,
        max_iters=120,
        via_trajopt=True,
    )
    correction_stage = None
    ee, ee_quat, _ = ctrl.read_pose()
    approach_fields = _motion_fields("approach", approach, ee)
    approach_within = bool(
        approach_reached
        and _motion_within(
            approach,
            ee,
            _APPROACH_POS_TOL,
            _APPROACH_Z_TOL,
        )
        and _rotation_within(ee_quat, q, 0.10)
    )
    if not approach_within:
        correction_target = bounded_residual_correction_target(
            approach,
            ee,
            max_total_error=0.03,
            max_xy_error=0.03,
            max_z_error=0.03,
            max_move=0.03,
        )
        if correction_target is not None:
            _, correction_state = ctrl.read_gripper_state()
            if correction_state is GripperState.HELD:
                correction_stage = "approach"
                ctrl.servo_correction_to(
                    correction_target,
                    quat=q,
                    gripper="close",
                    pos_tol=0.01,
                    rot_tol=0.10,
                    max_iters=80,
                )
                ee, ee_quat, _ = ctrl.read_pose()
                approach_fields = _motion_fields(
                    "approach",
                    approach,
                    ee,
                )
                approach_within = bool(
                    _motion_within(
                        approach,
                        ee,
                        _APPROACH_POS_TOL,
                        _APPROACH_Z_TOL,
                    )
                    and _rotation_within(ee_quat, q, 0.10)
                )
    if not approach_within:
        return (
            _failure(
                "could not reach the exact-place approach pose",
                correction_stage=correction_stage,
                **approach_fields,
            ),
            env.take_snapshot(),
        )

    gap_approach, state_approach = ctrl.read_gripper_state()
    if state_approach is not GripperState.HELD:
        return (
            _failure(
                "hold lost after exact-place approach",
                correction_stage=correction_stage,
                gripper_state_at_gate=state_approach.value,
                gripper_gap_at_gate=round(float(gap_approach), 4),
            ),
            env.take_snapshot(),
        )

    final_reached, _ = ctrl.servo_to(
        target,
        quat=q,
        gripper="close",
        pos_tol=pos_tol,
        rot_tol=0.05,
        max_iters=150,
        via_trajopt=True,
    )
    ee, ee_quat, _ = ctrl.read_pose()
    final_fields = _motion_fields("final", target, ee)
    final_within = bool(
        final_reached
        and _motion_within(
            target,
            ee,
            pos_tol,
            min(pos_tol, _MAX_FINAL_Z_ERROR),
        )
        and _rotation_within(ee_quat, q, 0.05)
    )
    measured_point = _valid_point(ee)
    if (
        not final_within
        and correction_stage is None
        and measured_point is not None
        and float(measured_point[2]) >= float(target[2])
    ):
        correction_target = bounded_residual_correction_target(
            target,
            ee,
            max_total_error=0.03,
            max_xy_error=0.03,
            max_z_error=0.03,
            max_move=0.03,
        )
        if correction_target is not None:
            _, correction_state = ctrl.read_gripper_state()
            if correction_state is GripperState.HELD:
                correction_stage = "final"
                ctrl.servo_correction_to(
                    correction_target,
                    quat=q,
                    gripper="close",
                    pos_tol=min(pos_tol, _MAX_FINAL_Z_ERROR),
                    rot_tol=0.05,
                    max_iters=80,
                )
                ee, ee_quat, _ = ctrl.read_pose()
                final_fields = _motion_fields("final", target, ee)
                final_within = bool(
                    _motion_within(
                        target,
                        ee,
                        pos_tol,
                        min(pos_tol, _MAX_FINAL_Z_ERROR),
                    )
                    and _rotation_within(ee_quat, q, 0.05)
                )
    if not final_within:
        return (
            _failure(
                "could not reach the exact release pose",
                correction_stage=correction_stage,
                **final_fields,
            ),
            env.take_snapshot(),
        )

    gap_pre_release, state_pre_release = ctrl.read_gripper_state()
    if state_pre_release is not GripperState.HELD:
        return (
            _failure(
                "hold lost before exact release",
                reached=True,
                correction_stage=correction_stage,
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
                "gripper did not open at the exact release pose",
                reached=True,
                correction_stage=correction_stage,
                gripper_state_pre_release=state_pre_release.value,
                gripper_state_after=state_after.value,
                gripper_gap_after=round(float(gap_after), 4),
                **final_fields,
            ),
            env.take_snapshot(),
        )

    retract_reached, _ = ctrl.servo_to(
        approach,
        quat=q,
        gripper="open",
        pos_tol=_APPROACH_POS_TOL,
        max_iters=60,
    )
    return (
        {
            "ok": bool(retract_reached),
            "reached": True,
            "released": True,
            "gripper_opened": True,
            "correction_stage": correction_stage,
            "reason": (
                "placed at exact target and retracted"
                if retract_reached
                else "object released but retraction failed"
            ),
            "target": [round(float(v), 4) for v in target],
            "gripper_state_before": state_before.value,
            "gripper_state_pre_release": state_pre_release.value,
            "gripper_state_after": state_after.value,
            "gripper_gap_before": round(float(gap_before), 4),
            "gripper_gap_after": round(float(gap_after), 4),
            "final_pos_error": final_fields["final_position_error"],
            **final_fields,
        },
        env.take_snapshot(),
    )
