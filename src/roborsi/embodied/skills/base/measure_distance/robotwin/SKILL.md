---
name: measure_distance
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Measure Euclidean distance between two runtime points.
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
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# measure_distance / RoboTwin

Measure Euclidean distance between two runtime points.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
