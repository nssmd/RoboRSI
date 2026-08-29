"""Recover a folded LIBERO arm through direct joint-position control."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def dispatch_runtime(state, args: dict[str, Any]):
    requested = args.get("max_iters", 240)
    try:
        max_iters = max(1, min(int(requested), 400))
    except (TypeError, ValueError, OverflowError):
        max_iters = 240
    control = LiberoControl(state.env)
    _, gripper_state = control.read_gripper_state()
    if gripper_state is GripperState.HELD:
        return (
            {
                "ok": False,
                "reached": False,
                "reason": "confirmed hold blocks joint-posture recovery",
                "joint_error_max": control.joint_posture_error(),
            },
            state.env.take_snapshot(),
        )
    reached, _ = control.recover_ready_posture(max_iters=max_iters)
    error = control.joint_posture_error()
    return (
        {
            "ok": bool(reached),
            "reached": bool(reached),
            "reason": None if reached else "ready joint posture was not reached",
            "joint_error_max": round(float(error), 5),
        },
        state.env.take_snapshot(),
    )
