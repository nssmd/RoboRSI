---
name: measure_relative_rotation
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Measure the relative rotation between two runtime directions.
args:
  v1:
    type: list
    required: true
  v2:
    type: list
    required: true
  axis:
    type: string
    default: z
    enum:
    - x
    - y
    - z
returns:
  ok: bool
  angle_deg: float
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# measure_relative_rotation / RoboTwin

Measure the relative rotation between two runtime directions.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
