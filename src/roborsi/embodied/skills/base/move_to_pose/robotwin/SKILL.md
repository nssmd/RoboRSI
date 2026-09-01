---
name: move_to_pose
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Move one arm end effector to a requested world-frame pose.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  x:
    type: float
    required: true
  y:
    type: float
    required: true
  z:
    type: float
    required: true
  quat:
    type: list
returns:
  ok: bool
  arm: string
  target_pose: list
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# move_to_pose / RoboTwin

Move one arm end effector to a requested world-frame pose.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
