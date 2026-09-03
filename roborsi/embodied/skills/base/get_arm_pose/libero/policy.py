"""get_arm_pose — end-effector pose + gripper state (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.raw_obs()
    ctrl = LiberoControl(state.env)
    pos = obs.get("robot0_eef_pos")
    quat = obs.get("robot0_eef_quat")
    gq = obs.get("robot0_gripper_qpos")
    gap, gripper_state = ctrl.read_gripper_state()
    result = {
        "ok": pos is not None,
        "pos": [round(float(v), 4) for v in pos] if pos is not None else None,
        "quat": [round(float(v), 4) for v in quat] if quat is not None else None,
        "gripper_qpos": [round(float(v), 4) for v in gq] if gq is not None else None,
        "gripper_gap": round(float(gap), 4),
        "gripper_state": gripper_state.value,
        "is_open": gripper_state is GripperState.OPEN,
    }
    return result, state.env.take_snapshot()
