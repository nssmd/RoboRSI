---
name: zoom_in
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Crop and enlarge a current image region around a selected pixel.
args:
  u:
    type: int
    required: true
  v:
    type: int
    required: true
  half_size_px:
    type: int
    default: 80
returns:
  ok: bool
  zoom_image_path: string
  zoom_window: dict
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# zoom_in / RoboTwin

Crop and enlarge a current image region around a selected pixel.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
