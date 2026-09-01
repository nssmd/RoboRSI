---
name: get_grasp_pose_segmented
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Generate grasp candidates from a visually segmented object point cloud.
args:
  object:
    type: string
    required: true
  color:
    type: string
    required: true
  camera:
    type: string
    default: head_camera
  bbox_pad_px:
    type: int
    default: 30
  top_k:
    type: int
    default: 5
returns:
  ok: bool
  backend: string
  grasp_pose: string
  score: float
  candidates: list
  num_object_points: int
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# get_grasp_pose_segmented / RoboTwin

Generate grasp candidates from a visually segmented object point cloud.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
