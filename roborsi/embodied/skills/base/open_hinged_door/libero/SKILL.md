---
name: open_hinged_door
kind: base
robot: libero
category: manipulation
version: 0.1.0
description: Open a visually localized appliance or cabinet door by grasping its attached handle and following a camera-depth-derived arc around a vertical hinge. Use for doors, never drawers.
args:
  object: { type: string, required: true, description: "exact visible door-handle phrase used with find_by_pointing" }
  pixel: { type: list, required: true, description: "fresh [u,v] returned by find_by_pointing for that exact handle" }
  hinge_side: { type: string, required: true, enum: [left, right], description: "visible side of the door carrying the vertical hinge" }
  angle_deg: { type: float, description: "bounded requested opening angle; default 65 degrees" }
  approach: { type: float, description: "bounded pre-contact standoff; default 0.09 m" }
returns:
  ok: bool
  opened: bool
  achieved_angle_deg: number
  hinge_radius: number
when_to_use: |
  After look() and find_by_pointing() identify an attached hinged-door handle.
  Inspect the image to choose hinge_side. This skill verifies that the exact
  semantic point is still current, approaches with an open gripper, closes on
  the handle, follows a bounded vertical-hinge arc, releases, and retracts.
  Verify the open cavity in a fresh image before declaring task completion.
when_NOT_to_use: Do not use for drawers, knobs, loose objects, sliding doors, or a stale/manually guessed pixel.
metadata:
  tags: [door, hinge, handle, rgbd, pure-vision, base_skill]
---

# Open Hinged Door

Pure-vision revolute-door manipulation. The handle pixel and current RGB-D
frame establish the interaction point. Nearby same-height depth samples on the
declared hinge side estimate the far door edge; no simulator object state or
task predicate is read. Motion success means the arm measured a sufficiently
large bounded arc and retracted, not that the simulator task succeeded.
