---
name: place_obb
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Fit target geometry and place a held object relative to its visible oriented bounding box.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  container:
    type: string
    required: true
  object:
    type: string
  u:
    type: int
  v:
    type: int
  inset_m:
    type: float
  drop_z:
    type: float
  z_min:
    type: float
  z_max:
    type: float
returns:
  ok: bool
  placed: bool
  obb: dict
  drop_xyz: list
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

# place_obb / RoboTwin

Fit target geometry and place a held object relative to its visible oriented bounding box.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
