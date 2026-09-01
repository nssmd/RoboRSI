---
name: visual_diff
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Compare two observations and summarize the visible scene change.
args:
  mode:
    type: string
    required: true
    enum:
    - snapshot
    - diff
  expected_change:
    type: string
    required: false
  camera:
    type: string
    required: false
  anchor_id:
    type: string
    required: false
returns:
  ok: bool
  changed: bool
  matches_expectation: bool
  vlm_reason: string
  panel_path: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# visual_diff / RoboTwin

Compare two observations and summarize the visible scene change.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
