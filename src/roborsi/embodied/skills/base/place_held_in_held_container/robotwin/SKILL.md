---
name: place_held_in_held_container
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Coordinate both arms when one holds an object and the other holds its container.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  container_arm:
    type: string
    required: true
    enum:
    - left
    - right
  drop_height_m:
    type: float
    required: false
  object:
    type: string
    required: false
  container:
    type: string
    required: false
returns:
  ok: bool
  success: bool
  holding_arm_ee_before: list
  container_arm_ee: list
  chosen_quat: string
  tried: list
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

# place_held_in_held_container / RoboTwin

Coordinate both arms when one holds an object and the other holds its container.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
