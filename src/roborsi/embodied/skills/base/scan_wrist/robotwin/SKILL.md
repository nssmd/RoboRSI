---
name: scan_wrist
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Capture a fresh wrist-camera view for close-range inspection.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
returns:
  ok: bool
  image_path: string
  camera: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# scan_wrist / RoboTwin

Capture a fresh wrist-camera view for close-range inspection.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
