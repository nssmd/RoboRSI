---
name: descend_tcp_to_z
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Lower one fingertip TCP to a target height with bounded residual corrections.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  target_z:
    type: float
    required: true
  x:
    type: float
  y:
    type: float
  quat:
    type: list
  floor_z:
    type: float
  tol_m:
    type: float
  max_iters:
    type: int
returns:
  ok: bool
  reached: bool
  tcp_z: float
  z_history: list
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

# descend_tcp_to_z / RoboTwin

Lower one fingertip TCP to a target height with bounded residual corrections.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
