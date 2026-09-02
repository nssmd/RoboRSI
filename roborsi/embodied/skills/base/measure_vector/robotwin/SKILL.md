---
name: measure_vector
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Vector from p1 → p2 with length and unit direction.
args:
  p1: { type: list, required: true }
  p2: { type: list, required: true }
returns:
  ok: bool
  vector: list
  length: float
  unit: list
when_to_use: |
  Plan offsets along an axis — e.g. "move 5 cm along (object → target) axis"
  becomes vec=measure_vector(obj, target).unit, then move_to_pose(target +
  0.05 * vec).
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"p1": [0, 0, 0], "p2": [1, 0, 0]}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---
