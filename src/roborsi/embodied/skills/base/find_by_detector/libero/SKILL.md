---
name: find_by_detector
kind: base
robot: libero
category: perception
version: 0.1.0
description: Localize one unambiguous object with an open-vocabulary OWLv2 detector refined by a SAM mask.
  Pure vision; returns an image pixel.
args:
  object:
    type: string
    required: true
    description: A concrete noun phrase such as "white mug" or "ketchup bottle".
returns:
  ok: bool
  u: int
  v: int
when_to_use: |
  After look(), use this when the target is the only instance of its kind in
  view. If several similar objects are present or the instruction relies on a
  spatial relation, use find_by_pointing instead.
metadata:
  tags:
  - perception
  - detector
  - owlv2
  - sam
  - pure-vision
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# find_by_detector

Detect the named object in the latest head-camera image with OWLv2, refine the
box with SAM, and return the mask centroid. Pass the returned pixel to
`unproject_pixel`, `get_grasp_pose`, or `grasp_object`.
