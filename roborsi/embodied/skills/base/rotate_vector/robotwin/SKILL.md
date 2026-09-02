---
name: rotate_vector
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Rotate a 3D (or 2D) vector by an angle around an axis.
args:
  vector: { type: list, required: true }
  angle_deg: { type: float, required: true }
  axis: { type: string, default: z }
returns:
  ok: bool
  rotated: list
when_to_use: |
  Compose tilt-pour or aligned-axis grasp poses. E.g. tilt the gripper
  +30° around y to pour bowl contents.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"v": [1, 0, 0], "quat": [1, 0, 0, 0]}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
