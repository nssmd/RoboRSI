"""base.robotwin.read_joint_state — qpos + grippers."""

from __future__ import annotations

from typing import Any


def run(env, **_: Any) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("read_joint_state requires an active RoboTwinEnv")
    impl = env._impl
    robot = impl.robot
    left = list(robot.get_left_arm_jointState())
    right = list(robot.get_right_arm_jointState())
    return {
        "left_arm": left[:-1] if left else [],
        "left_gripper": left[-1] if left else 0.0,
        "right_arm": right[:-1] if right else [],
        "right_gripper": right[-1] if right else 0.0,
    }
