---
name: verify_pick_complete
kind: base
robot: robotwin
category: state
version: 0.1.0
description: Combine proprioceptive and visual grasp evidence into one bounded pick check.
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
  min_visual_confidence:
    type: float
    default: 0.5
returns:
  ok: bool
  holding: bool
  visual_consistent: bool
  confidence: float
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - state
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# verify_pick_complete / RoboTwin

Combine proprioceptive and visual grasp evidence into one bounded pick check.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
