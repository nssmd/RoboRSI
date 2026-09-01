---
name: grasp_obb
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Fit an oriented bounding box and grasp across a visible object's narrow axis.
args:
  arm:
    type: string
    required: true
    enum:
    - left
    - right
  object:
    type: string
    required: true
  u:
    type: int
  v:
    type: int
returns:
  ok: bool
  held: bool
  grasp_xyz: list
  obb: dict
  trace: list
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

# grasp_obb / RoboTwin

Fit an oriented bounding box and grasp across a visible object's narrow axis.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
