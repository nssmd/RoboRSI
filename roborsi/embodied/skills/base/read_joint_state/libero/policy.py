"""read_joint_state — 7-DOF joint angles + gripper finger positions (base/libero)."""

from __future__ import annotations

from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.raw_obs()
    jp = obs.get("robot0_joint_pos")
    gq = obs.get("robot0_gripper_qpos")
    return ({"ok": jp is not None,
             "joint_pos": [round(float(v), 4) for v in jp] if jp is not None else None,
             "gripper_qpos": [round(float(v), 4) for v in gq] if gq is not None else None},
            state.env.take_snapshot())
