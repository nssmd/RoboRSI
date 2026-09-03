"""Pure-vision drawer close by pushing opposite the measured face normal."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base.pull_drawer.libero.policy import (
    _bounded_float,
    _pixel,
    _resolve_drawer_handle,
)

_CONTACT_STANDOFF = 0.01
_MIN_CLOSE_COMMAND = 0.18
_MIN_CLOSE_DISPLACEMENT = 0.14


def _visual_drawer_close(
    state: Any,
    object_name: str,
    handle_before: np.ndarray,
    outward_normal: np.ndarray,
) -> float | None:
    from roborsi.embodied.skills.base._lib.libero._perception import (
        localize_precise,
    )

    uv = localize_precise(state, object_name, route="vlm_sam")
    if uv is None:
        return None
    snapshot = state.env.take_snapshot()
    image = snapshot.images.get("head_camera")
    if image is None:
        return None
    resolved = _resolve_drawer_handle(
        state,
        object_name,
        image,
        (int(uv[0]), int(uv[1])),
    )
    if resolved is None:
        return None
    _, handle_after, normal_after = resolved
    normal = np.asarray(outward_normal, dtype=float)
    if float(np.dot(normal, np.asarray(normal_after, dtype=float))) < 0.9:
        return None
    delta = np.asarray(handle_after, dtype=float) - np.asarray(
        handle_before, dtype=float
    )
    distance = float(np.dot(delta, -normal))
    transverse = float(np.linalg.norm(delta - distance * -normal))
    if distance < 0.0 or distance > 0.25 or transverse > 0.07:
        return None
    return distance


def _failure(env: Any, reason: str, **fields: Any):
    return (
        {"ok": False, "closed": False, "reason": reason, **fields},
        env.take_snapshot(),
    )


def dispatch_runtime(state: Any, args: dict[str, Any]):
    env = state.env
    object_name = str(args.get("object") or "").strip()
    words = {
        word.strip(".,;:()[]{}").lower() for word in object_name.split()
    }
    if "drawer" not in words:
        return _failure(env, "object must name the exact drawer front or handle")
    uv = _pixel(state, args)
    if uv is None:
        return _failure(env, "give a finite in-frame drawer pixel=[u,v]")

    control = LiberoControl(env)
    gap, gripper_state = control.read_gripper_state()
    if gripper_state is GripperState.HELD:
        return _failure(
            env,
            "refusing drawer close while the gripper is holding",
            gripper_gap=round(float(gap), 4),
        )

    snapshot = env.take_snapshot()
    image = snapshot.images.get("head_camera")
    if image is None:
        return _failure(env, "head-camera image unavailable")
    resolved = _resolve_drawer_handle(
        state,
        object_name,
        image,
        uv,
    )
    if resolved is None:
        return _failure(env, "drawer pixel was not visually verified")
    verified_uv, handle, outward_normal = resolved
    normal = np.asarray(outward_normal, dtype=float)
    normal[2] = 0.0
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-8:
        return _failure(env, "drawer face normal is degenerate")
    normal /= normal_norm

    approach_distance = _bounded_float(
        args.get("approach"), 0.09, 0.06, 0.16
    )
    push_distance = _bounded_float(
        args.get("push_distance"), 0.20, _MIN_CLOSE_COMMAND, 0.22
    )
    approach = handle + normal * approach_distance
    contact = handle + normal * _CONTACT_STANDOFF
    push_target = contact - normal * push_distance
    _, quat, _ = control.read_pose()
    quat = np.asarray(quat, dtype=float)

    if gripper_state is GripperState.OPEN:
        control.set_gripper(close=True)

    reached, _ = control.servo_to(
        approach,
        quat=quat,
        gripper="close",
        pos_tol=0.04,
        max_iters=120,
        via_trajopt=True,
    )
    measured, _, _ = control.read_pose()
    if not reached or float(np.linalg.norm(measured - approach)) > 0.05:
        return _failure(
            env,
            "could not reach the drawer-close approach pose",
            requested_approach=approach.tolist(),
            measured_pose=np.asarray(measured, dtype=float).tolist(),
        )

    reached, _ = control.servo_to(
        contact,
        quat=quat,
        gripper="close",
        pos_tol=0.03,
        max_iters=100,
    )
    measured, _, _ = control.read_pose()
    if not reached or float(np.linalg.norm(measured - contact)) > 0.04:
        return _failure(env, "could not make drawer-front contact")

    push_start = np.asarray(measured, dtype=float)
    push_reached, _ = control.servo_to(
        push_target,
        quat=quat,
        gripper="close",
        pos_tol=0.04,
        max_iters=160,
    )
    measured, _, _ = control.read_pose()
    achieved = max(
        0.0,
        float(np.dot(np.asarray(measured, dtype=float) - push_start, -normal)),
    )
    retract_target = (
        np.asarray(measured, dtype=float)
        + normal * 0.08
        + np.array([0.0, 0.0, 0.04], dtype=float)
    )
    retracted, _ = control.servo_to(
        retract_target,
        quat=quat,
        gripper="close",
        pos_tol=0.05,
        max_iters=90,
    )
    control.set_gripper(close=False)
    visual_distance = _visual_drawer_close(
        state,
        object_name,
        handle,
        normal,
    )
    closed = bool(achieved >= _MIN_CLOSE_DISPLACEMENT)
    if closed and retracted:
        reason = "drawer push measured and arm retracted"
    elif closed:
        reason = "drawer close motion measured; arm retraction failed"
    else:
        reason = "drawer push did not achieve enough measured motion"
    return (
        {
            "ok": closed,
            "closed": closed,
            "reason": reason,
            "push_reached": bool(push_reached),
            "retracted": bool(retracted),
            "requested_push_distance": round(push_distance, 4),
            "required_close_distance": _MIN_CLOSE_DISPLACEMENT,
            "measured_push_distance": round(achieved, 4),
            "orientation_mode": "preserve_reachable",
            "visual_handle_close_distance": (
                round(float(visual_distance), 4)
                if visual_distance is not None
                else None
            ),
            "handle_pixel": [int(verified_uv[0]), int(verified_uv[1])],
            "handle_point": [round(float(value), 4) for value in handle],
            "face_normal": [round(float(value), 4) for value in normal],
        },
        env.take_snapshot(),
    )
