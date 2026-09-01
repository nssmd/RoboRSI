---
name: move_fingertip_to
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Move one fingertip to a target pose with gripper-geometry compensation.
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
  finger_offset:
    type: float
returns:
  ok: bool
  arm: string
  fingertip_target: list
  flange_target: list
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# move_fingertip_to / RoboTwin

Move one fingertip to a target pose with gripper-geometry compensation.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
