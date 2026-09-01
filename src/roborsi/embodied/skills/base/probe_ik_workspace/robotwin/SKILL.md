---
name: probe_ik_workspace
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Probe a bounded set of heights and approaches for feasible arm poses.
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
  z_min:
    type: float
    required: false
  z_max:
    type: float
    required: false
  z_step:
    type: float
    required: false
  approaches:
    type: list
    required: false
returns:
  ok: bool
  per_approach: dict
  best: dict
  summary: string
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# probe_ik_workspace / RoboTwin

Probe a bounded set of heights and approaches for feasible arm poses.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
