---
name: rotate_vector
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Rotate a runtime vector around a selected axis.
args:
  vector:
    type: list
    required: true
  angle_deg:
    type: float
    required: true
  axis:
    type: string
    default: z
returns:
  ok: bool
  rotated: list
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# rotate_vector / RoboTwin

Rotate a runtime vector around a selected axis.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
