---
name: grasp_top_down
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Try reachable grasp candidates whose approach is predominantly top-down.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
    - auto
  object:
    type: string
    required: true
  u:
    type: int
  v:
    type: int
returns:
  ok: bool
  chosen_arm: string
  chosen_approach_z: float
  attempts: list
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# grasp_top_down / RoboTwin

Try reachable grasp candidates whose approach is predominantly top-down.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
