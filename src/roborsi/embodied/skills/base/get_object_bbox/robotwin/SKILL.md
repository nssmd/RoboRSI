---
name: get_object_bbox
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Return a visible object's image bounding box and centroid.
args:
  object:
    type: string
    required: true
returns:
  ok: bool
  bbox: string
  centroid: string
  width_px: int
  height_px: int
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# get_object_bbox / RoboTwin

Return a visible object's image bounding box and centroid.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
