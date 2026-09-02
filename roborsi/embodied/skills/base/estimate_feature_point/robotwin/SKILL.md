---
name: estimate_feature_point
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: ReKep-style task-relevant keypoint. VLM picks a pixel on the FUNCTIONALLY relevant part of an object, not the geometric center.
args:
  object: { type: string, required: true }
  feature: { type: string, default: "the most graspable / task-relevant point" }
returns:
  ok: bool
  u: int
  v: int
when_to_use: |
  When find_pixel returns the geometric center but you need an affordance
  point — e.g. "rim of bowl" (for grasping), "handle tip" (for tool use),
  "long-axis center" (for grasping cylinders along their length).
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl", "feature": "center of rim"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
