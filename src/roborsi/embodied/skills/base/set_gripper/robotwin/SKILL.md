---
name: set_gripper
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Set one gripper to a requested open or closed state.
args:
  state:
    type: string
    required: true
    enum:
    - open
    - close
  arm:
    type: string
    default: both
    enum:
    - left
    - right
    - both
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

# set_gripper / RoboTwin

Set one gripper to a requested open or closed state.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
