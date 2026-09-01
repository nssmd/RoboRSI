---
name: measure_distance
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Euclidean distance between two 3D (or 2D) points, plus the delta vector p2-p1. Pure math
  — use to reason about how far apart two coordinates are.
args:
  p1:
    type: list
    required: true
  p2:
    type: list
    required: true
returns:
  ok: bool
  distance: float
  delta: list
when_to_use: |
  To compare coordinates obtained from camera-depth unprojection or robot
  proprioception, such as the distance from the end-effector to a visually
  localized target.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# measure_distance

Euclidean distance + delta vector between two points.
