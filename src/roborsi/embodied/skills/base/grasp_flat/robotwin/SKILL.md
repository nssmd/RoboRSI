---
name: grasp_flat
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Use a parameterized low-clearance pinch for thin objects on a support surface.
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
  table_z: float
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

# grasp_flat / RoboTwin

Use a parameterized low-clearance pinch for thin objects on a support surface.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
