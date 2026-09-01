---
name: place_beside
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Place a held object beside a visually localized target.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  target:
    type: string
    required: true
  held_object:
    type: string
    required: true
  offset_m:
    type: float
    default: 0.08
  drop_height_m:
    type: float
    default: 0.03
returns:
  ok: bool
  released: bool
  target_xyz: list
  place_pt: list
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# place_beside / RoboTwin

Place a held object beside a visually localized target.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
