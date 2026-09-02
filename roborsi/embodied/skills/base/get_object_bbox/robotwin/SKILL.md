---
name: get_object_bbox
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: VLM returns axis-aligned bounding box + centroid pixel of a named object in the latest image.
args:
  object: { type: string, required: true }
returns:
  ok: bool
  bbox: [u_min, v_min, u_max, v_max]
  centroid: [u, v]
  width_px: int
  height_px: int
when_to_use: |
  When you need the geometric center of a small object (more stable than
  find_pixel jitter), or when you need to know an object's image extent
  (width/height tells you how big it is). For elongated objects, the bbox's
  long axis hints at the object's orientation.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
