---
name: solve_relational_keypoint
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Compute a target point that satisfies a visible object-to-object relation.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  moving_keypoint:
    type: string
    required: true
  target_object:
    type: string
    required: false
  target_keypoint:
    type: string
    required: true
  offset_xyz:
    type: list
    required: false
  dry_run:
    type: bool
    required: false
returns:
  ok: bool
  dry_run: bool
  moving_keypoint_xyz: list
  target_keypoint_xyz: list
  goal_xyz: list
  delta_xyz: list
  target_ee_pose: list
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# solve_relational_keypoint / RoboTwin

Compute a target point that satisfies a visible object-to-object relation.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
