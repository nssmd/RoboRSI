---
name: is_holding
kind: base
robot: robotwin
category: state
version: 0.1.0
description: Estimate whether one gripper holds an object from robot state and current visual evidence.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  object:
    type: string
returns:
  ok: bool
  arm: string
  holding: bool
  finger_opening: float
  gripper_cmd: float
  interpretation: string
metadata:
  tags:
  - base
  - robotwin
  - state
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# is_holding / RoboTwin

Estimate whether one gripper holds an object from robot state and current visual evidence.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
