---
name: rotate_vector
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Rotate a 3D (or 2D) vector by an angle (degrees) around a named axis (x/y/z). Pure math.
args:
  vector:    { type: list, required: true }
  angle_deg: { type: float, required: true }
  axis:      { type: string, default: z, enum: [x, y, z] }
returns:
  ok: bool
  rotated: list
when_to_use: |
  To rotate an approach/offset direction in the world frame (e.g. rotate a pull
  direction by 90°).
---

# rotate_vector

Rotate a vector about x/y/z by angle_deg.
