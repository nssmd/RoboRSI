---
name: move_dual_arm
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Execute one synchronized dual-arm move to two target poses.
args:
  left_pose:
    type: list
    required: true
  right_pose:
    type: list
    required: true
returns:
  ok: bool
  plan_success: bool
  left_reached: bool
  right_reached: bool
  left_ee_after: list
  right_ee_after: list
  note: string
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# move_dual_arm / RoboTwin

Execute one synchronized dual-arm move to two target poses.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
