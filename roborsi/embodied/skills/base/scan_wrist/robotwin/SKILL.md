---
name: scan_wrist
kind: base
robot: robotwin
category: active_perception
version: 0.1.0
description: Snap a frame from a wrist camera (left_camera or right_camera). Active perception for close-up / occluded views.
args:
  arm: { type: string, required: true, enum: [left, right] }
returns:
  ok: bool
  image_path: str
  camera: str
when_to_use: |
  When head_camera is occluded by an arm reaching across the scene, or
  when you need a close-up of what an arm is holding. Wrist cameras give
  a different vantage point that head_camera can't.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
