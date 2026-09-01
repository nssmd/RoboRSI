---
name: place_held_at_target_servo
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Visually servo a held object to a target and optionally release it.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  held_object:
    type: string
    required: true
  target:
    type: string
    required: true
  camera:
    type: string
    default: head_camera
  tol_m:
    type: float
    default: 0.02
  max_iters:
    type: int
    default: 5
  max_step_m:
    type: float
    default: 0.04
  release:
    type: bool
    default: true
  descend_m:
    type: float
    default: 0.04
  quat:
    type: list
  hover_z:
    type: float
returns:
  ok: bool
  reached: bool
  released: bool
  iterations: int
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

# place_held_at_target_servo / RoboTwin

Visually servo a held object to a target and optionally release it.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
