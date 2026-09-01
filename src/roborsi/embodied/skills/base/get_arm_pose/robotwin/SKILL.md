---
name: get_arm_pose
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Read the current end-effector pose for one arm.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
returns:
  ok: bool
  ee_pose: list
  xyz: list
  quat: list
  fingertip_xyz_top_down: list
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# get_arm_pose / RoboTwin

Read the current end-effector pose for one arm.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
