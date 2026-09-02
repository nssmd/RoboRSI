---
name: find_pixel
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Ask a vision model to point at a pixel of an object in the latest image.
args:
  object:   { type: string, required: true, description: "what to find (e.g. 'red block')" }
  location: { type: string, description: "which part (e.g. 'top center')" }
returns:
  ok: bool
  u: int
  v: int
  confidence: float
when_to_use: |
  After look(). For TINY objects (≤3 cm) prefer get_object_bbox or zoom_in
  before find_pixel — find_pixel jitters by several pixels which is fine for
  cups but breaks for cubes.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl on the right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
