---
name: label_points_grid
kind: base
robot: robotwin
category: active_perception
version: 0.1.0
description: Set-of-Mark prompting — overlay numbered red dots on a regular grid over the latest image. Returns label→(u,v) so VLM can pick a label visually.
args:
  grid_n:    { type: int, default: 5, description: "N×N grid; ≤9" }
  margin_px: { type: int, default: 60 }
returns:
  ok: bool
  labeled_image_path: str
  labels: { "1": [u, v], "2": [u, v], ... }
when_to_use: |
  When find_pixel keeps confusing two nearby objects. The numbered overlay
  forces VLM to commit to one specific spot from candidates, then you call
  unproject_pixel / move_to_pixel with that label's (u, v).
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"camera": "head_camera", "mask_from_query": "silver bowl", "n": 5}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
