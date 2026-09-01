---
name: estimate_feature_point
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Select a task-relevant visual feature point on a named object.
args:
  object:
    type: string
    required: true
  feature:
    type: string
    default: the most graspable / task-relevant point
returns:
  ok: bool
  u: int
  v: int
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# estimate_feature_point / RoboTwin

Select a task-relevant visual feature point on a named object.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
