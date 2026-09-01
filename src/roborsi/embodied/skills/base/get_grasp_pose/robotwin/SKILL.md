---
name: get_grasp_pose
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Generate ranked 6-DoF grasp candidates from current RGB-D observations.
args:
  object:
    type: string
  u:
    type: int
  v:
    type: int
  camera:
    type: string
    default: head_camera
  half_window_px:
    type: int
    default: 60
  top_k:
    type: int
    default: 5
  z_min:
    type: float
  z_max:
    type: float
returns:
  ok: bool
  backend: string
  grasp_pose: string
  score: float
  candidates: list
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# get_grasp_pose / RoboTwin

Generate ranked 6-DoF grasp candidates from current RGB-D observations.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
