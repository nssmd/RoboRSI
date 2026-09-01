---
name: grasp_object
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Compose visual grounding, grasp generation, reachability, closure, lift, and grasp verification.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  object:
    type: string
    required: true
  u:
    type: int
  v:
    type: int
  top_k:
    type: int
    default: 30
  half_window_px:
    type: int
    default: 60
  z_min:
    type: float
  z_max:
    type: float
  strategy:
    type: string
    default: diverse
    enum:
    - top_down
    - diverse
returns:
  ok: bool
  attempts: list
  succeeded_with: int
  holding_visual: bool
  confidence: float
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

# grasp_object / RoboTwin

Compose visual grounding, grasp generation, reachability, closure, lift, and grasp verification.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
