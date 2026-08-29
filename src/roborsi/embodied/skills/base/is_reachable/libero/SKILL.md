---
name: is_reachable
kind: base
robot: libero
category: control
version: 0.2.0
description: Reject obviously unreachable finite target coordinates with a conservative workspace-envelope heuristic before a full arm motion.
args:
  x: { type: float, description: "World-frame target X; provide x/y/z or pos." }
  y: { type: float, description: "World-frame target Y." }
  z: { type: float, description: "World-frame target Z." }
  pos: { type: list, description: "Finite target [x,y,z]; alternative to x/y/z." }
returns:
  ok: bool
  reachable: bool
  distance_to_base: float
  base_pos: list
  reason: string
when_to_use: |
  Before moving to a camera-derived target far across the workspace. A positive
  result is only a coarse envelope check; the motion controller still decides
  whether the exact pose is reachable.
metadata:
  tags: [single-arm, libero, control, heuristic]
---

# is_reachable

Compare a finite target with the Panda base and conservative distance/height
bounds.
