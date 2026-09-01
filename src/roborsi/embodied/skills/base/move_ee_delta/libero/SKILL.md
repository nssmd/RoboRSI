---
name: move_ee_delta
kind: base
robot: libero
category: control
version: 0.1.0
description: Nudge the end-effector by a small world-frame offset [dx, dy, dz] from its current position
  (closed-loop to current+delta). Use for fine corrections.
args:
  dpos:
    type: list
    required: true
    description: World-frame offset [dx, dy, dz] in meters.
  gripper:
    type: string
    enum:
    - open
    - close
    - keep
    description: Gripper state to hold (default keep).
returns:
  ok: bool
  reached: bool
  ee_pos: list
when_to_use: |
  Small adjustments (e.g. "lower 2 cm", "shift +x 1 cm") after inspecting the
  result of a grasp/place. For a full move to an absolute point use move_to_pose.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# move_ee_delta

Relative end-effector nudge (servo to current position + delta).
