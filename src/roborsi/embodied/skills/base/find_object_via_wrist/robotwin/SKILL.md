---
name: find_object_via_wrist
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Refine a coarse object localization with a close wrist-camera observation.
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
  location:
    type: string
    required: false
  hover_height_m:
    type: float
    required: false
returns:
  ok: bool
  u: int
  v: int
  xyz: string
  coarse_xyz: string
  wrist_image: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# find_object_via_wrist / RoboTwin

Refine a coarse object localization with a close wrist-camera observation.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
