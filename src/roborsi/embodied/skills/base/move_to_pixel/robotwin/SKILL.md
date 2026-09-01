---
name: move_to_pixel
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Unproject a current image pixel and execute a bounded point-directed arm action.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  u:
    type: int
    required: true
  v:
    type: int
    required: true
  action:
    type: string
    required: true
    enum:
    - hover
    - grasp
    - pinch_grasp
    - release
    - tap
  height_above_m:
    type: float
    default: 0.0
  camera:
    type: string
    default: head_camera
returns:
  ok: bool
  reason: string
  ee_xyz: list
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# move_to_pixel / RoboTwin

Unproject a current image pixel and execute a bounded point-directed arm action.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
