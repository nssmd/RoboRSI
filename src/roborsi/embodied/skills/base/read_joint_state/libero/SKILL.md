---
name: read_joint_state
kind: base
robot: libero
category: perception
version: 0.1.0
description: Read the current 7 arm joint angles and the 2 gripper finger positions of the LIBERO Panda.
args: {}
returns:
  ok: bool
  joint_pos: list
  gripper_qpos: list
when_to_use: |
  Diagnostics — to inspect the raw joint configuration (rarely needed since
  control is end-effector servo; prefer get_arm_pose for task reasoning).
---

# read_joint_state

Raw 7-DOF joint angles + gripper finger positions.
