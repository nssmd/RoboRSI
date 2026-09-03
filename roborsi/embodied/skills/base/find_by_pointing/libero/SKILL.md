---
name: find_by_pointing
kind: base
robot: libero
category: perception
version: 0.1.0
description: Localize an object with a vision-language pointing model refined by a SAM mask. Handles spatial
  relations and look-alikes using only camera images.
args:
  object:
    type: string
    required: true
    description: The full target phrase, including spatial relations when needed.
returns:
  ok: bool
  u: int
  v: int
when_to_use: |
  After look(), use this when several similar objects are visible or when the
  target is named by a relation such as "left", "between", or "nearest".
metadata:
  tags:
  - perception
  - pointing
  - vlm
  - sam
  - pure-vision
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# find_by_pointing

Ask the vision-language pointer to select the target in the latest head-camera
image, refine that point with SAM, and return the mask centroid. Pass the pixel
to `unproject_pixel`, `get_grasp_pose`, or `grasp_object`.
