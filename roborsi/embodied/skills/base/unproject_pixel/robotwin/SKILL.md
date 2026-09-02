---
name: unproject_pixel
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Pixel → world XYZ using camera intrinsics + depth. Lets the VLM compose its own grasp instead of using the canned move_to_pixel action set.
args:
  camera: { type: string, default: "head_camera" }
  u: { type: int, required: true }
  v: { type: int, required: true }
returns:
  ok: bool
  xyz: [x, y, z]
when_to_use: |
  When you want fine control over descent depth, lift height, or non-standard
  grasp poses. After unproject, use move_to_pose(arm, x, y, z+offset) to
  descend exactly where you want, then gripper(close).
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"u": 280, "v": 120, "camera": "head_camera"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---
