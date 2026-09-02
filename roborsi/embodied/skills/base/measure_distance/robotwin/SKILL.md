---
name: measure_distance
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Euclidean distance between two 3D (or 2D) points + delta vector.
args:
  p1: { type: list, required: true }
  p2: { type: list, required: true }
returns:
  ok: bool
  distance: float
  delta: list
when_to_use: |
  Reasoning about offsets — "is the gripper close enough to the target?"
  ("distance < 2 cm" check before closing). Or computing how far to nudge.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"a": [0, 0, 0], "b": [1, 0, 0]}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['distance']
      min_seeds_passing: 1
---
