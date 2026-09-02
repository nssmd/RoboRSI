---
name: look
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Snap an RGB frame from a named camera. The image is attached to the next user turn so the VLM can see it.
args:
  camera: { type: string, default: "head_camera", description: "camera name; default head_camera" }
returns:
  ok: bool
  image_path: str
when_to_use: |
  Always call before find_pixel / zoom_in / get_object_bbox so they have a fresh frame
  to reason about. Use after every move_* call to verify what changed.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"camera": "head_camera"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: []
      min_seeds_passing: 1
---
