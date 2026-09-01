---
name: look
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Capture a fresh observation from a named RoboTwin camera.
args:
  camera:
    type: string
    default: head_camera
returns:
  ok: bool
  image_path: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# look / RoboTwin

Capture a fresh observation from a named RoboTwin camera.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
