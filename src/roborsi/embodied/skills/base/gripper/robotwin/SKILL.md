---
name: gripper
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Command one RoboTwin gripper without moving the arm.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  action:
    type: string
    required: true
    enum:
    - open
    - close
  pos:
    type: float
returns:
  ok: bool
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# gripper / RoboTwin

Command one RoboTwin gripper without moving the arm.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
