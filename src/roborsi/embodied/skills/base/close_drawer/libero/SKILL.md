---
name: close_drawer
kind: base
robot: libero
category: control
version: 0.1.0
description: Pure-vision drawer close from a fresh drawer-front or handle pixel. Fits the cabinet face,
  approaches from free space, and pushes inward opposite the measured outward normal.
args:
  object:
    type: string
    required: true
    description: Exact requested drawer front or handle phrase.
  pixel:
    type: list
    required: true
    description: Fresh head-camera drawer pixel [u, v].
  approach:
    type: float
    default: 0.09
    description: Bounded free-space standoff in meters.
  push_distance:
    type: float
    default: 0.18
    description: Bounded inward push distance in meters.
returns:
  ok: bool
  reached: bool
  pushed_distance: float
  reason: string
when_to_use: Use only when the task explicitly requires closing a visible open drawer.
when_NOT_to_use: Do not use for opening drawers, hinged doors, loose objects, or while holding an object.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# Close Drawer

Use a fresh visual drawer pixel. The tool derives the cabinet face normal from
RGB-D, pushes inward, and reports measured motion.
