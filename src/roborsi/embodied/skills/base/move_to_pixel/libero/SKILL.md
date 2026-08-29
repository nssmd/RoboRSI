---
name: move_to_pixel
kind: base
robot: libero
category: control
version: 0.1.0
description: Move the end-effector above a point you clicked in a camera image — unprojects the pixel to a world XYZ, then servos there (with a vertical approach offset).
args:
  u:         { type: int, required: true, description: "Pixel column (0=left)." }
  v:         { type: int, required: true, description: "Pixel row (0=top)." }
  camera:    { type: string, default: head, enum: [head, wrist] }
  approach_z: { type: float, description: "Extra height to stop above the unprojected point (m, default 0.05)." }
  gripper:   { type: string, enum: [open, close, keep], description: "Gripper state to hold (default keep)." }
returns:
  ok: bool
  reached: bool
  world: list
  ee_pos: list
when_to_use: |
  Visual motion to a target identified in the current camera image. Use
  grasp_object for picking and a dedicated placement skill for releasing a held
  object.
---

# move_to_pixel

Unproject a pixel to world, then servo the end-effector above it.
