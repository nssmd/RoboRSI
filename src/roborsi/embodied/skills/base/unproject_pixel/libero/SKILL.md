---
name: unproject_pixel
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Convert an image pixel (u, v) to a world-frame XYZ using the camera depth + intrinsics/extrinsics.
  Lets you ground a point you see in a `look` image to coordinates.
args:
  u:
    type: int
    required: true
    description: Pixel column (0=left).
  v:
    type: int
    required: true
    description: Pixel row (0=top).
  camera:
    type: string
    default: head
    enum:
    - head
    - wrist
returns:
  ok: bool
  world: list
when_to_use: |
  After locating a target pixel in a current `look` frame, convert that pixel
  and its camera depth to a world coordinate for move_to_pose / move_to_pixel.
  Re-localize and unproject again after scene motion instead of reusing a stale
  coordinate.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# unproject_pixel

Pixel → world XYZ via depth + camera matrices.
