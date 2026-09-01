---
name: measure_relative_rotation
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Signed angle (degrees) from vector v1 to vector v2 about a named axis (x/y/z). Pure math.
args:
  v1:
    type: list
    required: true
  v2:
    type: list
    required: true
  axis:
    type: string
    default: z
    enum:
    - x
    - y
    - z
returns:
  ok: bool
  angle_deg: float
when_to_use: |
  To measure how much to rotate one direction onto another (e.g. align an
  approach vector with an object's long axis).
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# measure_relative_rotation

Signed angle from v1 to v2 about the given axis.
