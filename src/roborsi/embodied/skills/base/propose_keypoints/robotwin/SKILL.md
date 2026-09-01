---
name: propose_keypoints
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Generate separated visual keypoint candidates on a named object.
args:
  object:
    type: string
    required: true
  k:
    type: int
    required: false
  camera:
    type: string
    required: false
  min_pixel_separation:
    type: int
    required: false
returns:
  ok: bool
  keypoints_uv: string
  camera: string
  n_clusters_returned: int
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# propose_keypoints / RoboTwin

Generate separated visual keypoint candidates on a named object.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
