---
name: measure_distance
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Euclidean distance between two 3D (or 2D) points, plus the delta vector p2-p1. Pure math — use to reason about how far apart two coordinates are.
args:
  p1: { type: list, required: true }
  p2: { type: list, required: true }
returns:
  ok: bool
  distance: float
  delta: list
when_to_use: |
  To decide reachability or alignment from perceived points and the robot's own
  end-effector pose.
---

# measure_distance

Euclidean distance + delta vector between two points.
