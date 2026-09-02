---
name: get_arm_pose
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Read an arm's current EE flange pose [x, y, z, qx, qy, qz, qw] in world frame. Use to compute targets relative to a held object, or to verify a move actually reached its goal.
args:
  arm: { type: string, required: true, enum: [left, right] }
returns:
  ok: bool
  ee_pose: list
  xyz: list
  quat: list
  fingertip_xyz_top_down: list
when_to_use: |
  When you need to know where a held object is — call get_arm_pose(arm) on
  the arm holding it, the object's xyz ≈ fingertip_xyz_top_down. Way more
  accurate than find_pixel on a bowl held in mid-air.

  Also use to verify reachability: if measure_distance(get_arm_pose(left),
  get_arm_pose(right)) < 0.10, the arms are close to colliding.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: []
      min_seeds_passing: 1
---

# get_arm_pose · RoboTwin

The "where am I" oracle. Returns the EE flange world pose. For top-down
grasps the fingertip is ~0.18m below the flange. If the arm is currently
holding an object, the object world XYZ ≈ fingertip XYZ.
