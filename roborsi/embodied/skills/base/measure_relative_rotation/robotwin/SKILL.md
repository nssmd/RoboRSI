---
name: measure_relative_rotation
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Signed angle (degrees) from v1 to v2 around a named axis.
args:
  v1: { type: list, required: true }
  v2: { type: list, required: true }
  axis: { type: string, default: z, enum: [x, y, z] }
returns:
  ok: bool
  angle_deg: float
when_to_use: |
  Figure out how much to yaw the gripper to align with an object's long
  axis (for elongated cylinders / pens). Compute angle, then rotate_vector
  the standard top-down quat by that angle.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"v1": [1, 0, 0], "v2": [0, 1, 0]}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---
