---
name: park_arm
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Move an idle arm to a configured clearance posture while preserving its grip state.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  keep_grip:
    type: bool
    required: false
  x:
    type: float
    required: false
  y:
    type: float
    required: false
  z:
    type: float
    required: false
  quat:
    type: list
    required: false
returns:
  ok: bool
  arm: string
  park_pose: list
  ee_after: list
  delta_m: float
  kept_grip: bool
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

# park_arm / RoboTwin

Move an idle arm to a configured clearance posture while preserving its grip state.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
