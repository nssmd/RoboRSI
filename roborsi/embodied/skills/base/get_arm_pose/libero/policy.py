"""get_arm_pose — end-effector pose + gripper state (base/libero)."""

from __future__ import annotations

from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.raw_obs()
    pos = obs.get("robot0_eef_pos")
    quat = obs.get("robot0_eef_quat")
    gq = obs.get("robot0_gripper_qpos")
    is_open = bool(float(gq[0] - gq[1]) > 0.03) if gq is not None else None
    result = {
        "ok": pos is not None,
        "pos": [round(float(v), 4) for v in pos] if pos is not None else None,
        "quat": [round(float(v), 4) for v in quat] if quat is not None else None,
        "gripper_qpos": [round(float(v), 4) for v in gq] if gq is not None else None,
        "is_open": is_open,
    }
    return result, state.env.take_snapshot()
