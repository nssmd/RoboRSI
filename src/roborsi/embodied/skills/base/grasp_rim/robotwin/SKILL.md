---
name: grasp_rim
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Pinch a visible container rim with a bounded inside-outside finger placement.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  object:
    type: string
    required: true
  u:
    type: int
  v:
    type: int
  z_min:
    type: float
  z_max:
    type: float
returns:
  ok: bool
  held: bool
  grasp_xyz: list
  rim_z: float
  trace: list
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

# grasp_rim / RoboTwin

Pinch a visible container rim with a bounded inside-outside finger placement.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
