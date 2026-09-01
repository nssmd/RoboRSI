---
name: unproject_pixel
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Convert a current RGB-D pixel into a world-frame surface point.
args:
  camera:
    type: string
    default: head_camera
  u:
    type: int
    required: true
  v:
    type: int
    required: true
returns:
  ok: bool
  xyz: string
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# unproject_pixel / RoboTwin

Convert a current RGB-D pixel into a world-frame surface point.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
