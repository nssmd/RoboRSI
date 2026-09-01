---
name: home
kind: base
robot: libero
category: control
version: 0.2.0
description: Retract the arm upward by a relative safe lift while preserving the current gripper state
  and any confirmed hold.
args:
  lift:
    type: float
    description: Relative upward lift in metres (default 0.18).
returns:
  ok: bool
  ee_pos: list
when_to_use: |
  To get the arm out of the camera's way or away from clutter. If an object is
  held, home preserves the grip; use a placement skill to release it.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# home

Retract the end-effector straight upward without changing the gripper command.
