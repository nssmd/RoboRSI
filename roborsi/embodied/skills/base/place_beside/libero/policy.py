"""place_beside — set the HELD object down BESIDE a reference, keeping the
current grasp orientation (base/libero).

The complement of ``place_object_in`` (which drops INTO a container's
contain-region from directly above): this offsets laterally from a reference
object/point so the held item lands on the surface NEXT TO it, and keeps the
current end-effector orientation so an upright grasp stays upright.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState

_APPROACH_POS_TOL = 0.02
_APPROACH_Z_TOL = 0.015
_FINAL_POS_TOL = 0.02
_FINAL_Z_TOL = 0.008

# Instruction-frame sides as seen in the head camera: base sits at -x, so +x
# points away from the robot, while image-left/image-right map to -y/+y.
# Callers can still override with explicit dx/dy/dz.
_SIDE = {
    "front": (1.0, 0.0),
    "back": (-1.0, 0.0),
    "left": (0.0, -1.0),
    "right": (0.0, 1.0),
}


def _reference_xyz(state, name: str, pos):
    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        point = np.asarray(pos, dtype=float)
        return point if point.shape == (3,) and np.all(np.isfinite(point)) else None
    if name:
        env = state.env
        # Pure-perception reference: localize + cloud centroid.
        from roborsi.embodied.skills.base._lib.libero._perception import (
            localize_precise,
            object_cloud,
        )

        loc = localize_precise(state, name)
        if loc is None or (abs(int(loc[0]) - 128) <= 2 and abs(int(loc[1]) - 128) <= 2):
            return None
        cloud = object_cloud(env, int(loc[0]), int(loc[1]), z_band=0.18)
        if cloud is None or len(cloud) < 20:
            return None
        return np.array(
            [
                float(np.median(cloud[:, 0])),
                float(np.median(cloud[:, 1])),
                float(np.percentile(cloud[:, 2], 5)),
            ]
        )
    return None


def _motion_fields(prefix: str, target, measured) -> dict[str, Any]:
    point = np.asarray(measured, dtype=float)
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
    name = str(args.get("target") or args.get("object") or "").strip()
    pos = args.get("pos")
    side = str(args.get("side", "right")).strip().lower()
    gap = float(args.get("gap", 0.12))
    dx = float(args.get("dx", 0.0))
    dy = float(args.get("dy", 0.0))
    dz = float(args.get("dz", 0.0))
    drop_h = float(args.get("drop_height", 0.04))
    hover = float(args.get("hover", 0.12))

    gap_before, state_before = ctrl.read_gripper_state()
    if state_before is not GripperState.HELD:
        return (
            _failure(
                f"gripper state is {state_before.value}, not a confirmed hold",
                holding=False,
                gripper_state_before=state_before.value,
                gripper_gap_before=round(float(gap_before), 4),
            ),
            env.take_snapshot(),
        )

    ref = _reference_xyz(state, name, pos)
    if ref is None:
        return (
            _failure(
                f"no visual reference '{name}'; give target=<name> or pos=[x,y,z]",
                gripper_state_before=state_before.value,
                gripper_gap_before=round(float(gap_before), 4),
            ),
            env.take_snapshot(),
        )

    sx, sy = _SIDE.get(side, (0.0, -1.0))
    place = np.array([ref[0] + sx * gap + dx,
                      ref[1] + sy * gap + dy,
                      ref[2] + drop_h + dz])
    if not np.all(np.isfinite(place)):
        return (
            _failure("computed place point is not finite"),
            env.take_snapshot(),
        )

    approach = place + np.array([0.0, 0.0, hover])
    approach_reached, _ = ctrl.servo_to(
        approach,
        gripper="close",
        pos_tol=_APPROACH_POS_TOL,
        max_iters=100,
    )
    ee, _, _ = ctrl.read_pose()
    approach_fields = _motion_fields("approach", approach, ee)
    if not approach_reached or not _motion_within(
        approach,
        ee,
        _APPROACH_POS_TOL,
        _APPROACH_Z_TOL,
    ):
        return (
            _failure(
                "could not reach the beside approach pose",
                **approach_fields,
            ),
            env.take_snapshot(),
        )

    gap_approach, state_approach = ctrl.read_gripper_state()
    if state_approach is not GripperState.HELD:
        return (
            _failure(
                "hold lost after beside approach",
                gripper_state_at_gate=state_approach.value,
                gripper_gap_at_gate=round(float(gap_approach), 4),
            ),
            env.take_snapshot(),
        )

    final_reached, _ = ctrl.servo_to(
        place,
        gripper="close",
        pos_tol=_FINAL_POS_TOL,
        max_iters=100,
    )
    ee, _, _ = ctrl.read_pose()
    final_fields = _motion_fields("final", place, ee)
    if not final_reached or not _motion_within(
        place,
        ee,
        _FINAL_POS_TOL,
        _FINAL_Z_TOL,
    ):
        return (
            _failure(
                "could not reach the beside release pose",
                **final_fields,
            ),
            env.take_snapshot(),
        )

    gap_pre_release, state_pre_release = ctrl.read_gripper_state()
    if state_pre_release is not GripperState.HELD:
        return (
            _failure(
                "hold lost before beside release",
                reached=True,
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
                "gripper did not open at the beside release pose",
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
    )
    return (
        {
            "ok": bool(retract_reached),
            "reached": True,
            "released": True,
            "gripper_opened": True,
            "reason": (
                "placed beside and retracted"
                if retract_reached
                else "object released but retraction failed"
            ),
            "place_pt": [round(float(v), 4) for v in place],
            "gripper_state_before": state_before.value,
            "gripper_state_pre_release": state_pre_release.value,
            "gripper_state_after": state_after.value,
            "gripper_gap_before": round(float(gap_before), 4),
            "gripper_gap_after": round(float(gap_after), 4),
            **final_fields,
        },
        env.take_snapshot(),
    )
