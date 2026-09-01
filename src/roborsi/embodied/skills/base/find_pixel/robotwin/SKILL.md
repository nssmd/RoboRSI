---
name: find_pixel
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Ground a visible object phrase to a pixel in the current camera frame.
args:
  object:
    type: string
    required: true
  location:
    type: string
returns:
  ok: bool
  u: int
  v: int
  confidence: float
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# find_pixel / RoboTwin

Ground a visible object phrase to a pixel in the current camera frame.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
