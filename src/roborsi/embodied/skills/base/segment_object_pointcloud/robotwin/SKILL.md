---
name: segment_object_pointcloud
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Build a bounded object point cloud from current RGB-D and a visual mask.
args:
  object:
    type: string
    required: true
  ee_xyz:
    type: list
  ee_radius_m:
    type: float
    default: 0.2
  vlm_verify:
    type: bool
    default: true
  min_pixels:
    type: int
    default: 30
  cameras:
    type: list
  max_points:
    type: int
    default: 5000
  cluster_strategy:
    type: string
    default: vlm
returns:
  ok: bool
  points: list
  point_count: int
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# segment_object_pointcloud / RoboTwin

Build a bounded object point cloud from current RGB-D and a visual mask.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
