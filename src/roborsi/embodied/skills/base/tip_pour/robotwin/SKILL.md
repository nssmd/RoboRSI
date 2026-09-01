---
name: tip_pour
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Tilt a held container toward a receiving area localized in the current observation.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  target_pixel:
    type: list
    required: true
  tilt_deg:
    type: float
    default: 70.0
  hold_steps:
    type: int
    default: 30
returns:
  ok: bool
  tilted: bool
  held_steps: int
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

# tip_pour / RoboTwin

Tilt a held container toward a receiving area localized in the current observation.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
