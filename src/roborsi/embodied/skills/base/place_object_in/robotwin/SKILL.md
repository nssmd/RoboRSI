---
name: place_object_in
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Place a held object into a container localized from the current observation.
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
  drop_height_m:
    type: float
    default: 0.12
  retreat_m:
    type: float
    default: 0.1
returns:
  ok: bool
  attempts: list
  reason: string
  released: bool
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# place_object_in / RoboTwin

Place a held object into a container localized from the current observation.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
