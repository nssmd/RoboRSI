---
name: measure_vector
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Vector from p1 to p2 with its length and unit direction. Pure math.
args:
  p1: { type: list, required: true }
  p2: { type: list, required: true }
returns:
  ok: bool
  vector: list
  length: float
  unit: list
when_to_use: |
  To compute an approach or pull direction between points obtained from
  camera-depth unprojection or robot proprioception.
---

# measure_vector

Vector p1→p2 with length and unit direction.
