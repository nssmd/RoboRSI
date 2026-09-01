---
name: is_reachable
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Check whether an arm can reach a candidate pose before execution.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  x:
    type: float
    required: true
  y:
    type: float
    required: true
  z:
    type: float
    required: true
  quat:
    type: list
returns:
  ok: bool
  reachable: bool
  reason: string
  distance_to_base: float
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# is_reachable / RoboTwin

Check whether an arm can reach a candidate pose before execution.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
