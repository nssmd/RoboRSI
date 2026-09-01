---
name: capture_image
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Capture the current RGB frame and optional depth from a named camera.
args: {}
returns:
  rgb: string
  depth: string
  path: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# capture_image / RoboTwin

Capture the current RGB frame and optional depth from a named camera.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
