---
name: get_arm_pose
kind: base
robot: libero
category: perception
version: 0.1.0
description: Read the current end-effector world pose (position + orientation quaternion) and gripper
  opening on the LIBERO Panda.
args: {}
returns:
  ok: bool
  pos: list
  quat: list
  gripper_qpos: list
  is_open: bool
when_to_use: |
  Before/after a motion to confirm where the gripper is and whether it is open
  or closed. This is robot proprioception and does not advance the simulation.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# get_arm_pose

Proprioceptive end-effector pose (`pos` xyz, `quat` xyzw) and gripper state.
