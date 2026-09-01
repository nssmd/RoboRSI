---
name: verify_grasp_visual
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Check a grasp using current robot state and before-after visual evidence.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  object:
    type: string
    required: false
returns:
  ok: bool
  holding_visual: bool
  holding: bool
  finger_opening: float
  confidence: float
  reason: string
  image_path: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# verify_grasp_visual / RoboTwin

Check a grasp using current robot state and before-after visual evidence.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
