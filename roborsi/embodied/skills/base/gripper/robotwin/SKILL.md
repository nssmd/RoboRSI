---
name: gripper
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Direct gripper open/close on a single arm. No motion. Pair with move_to_pose to compose custom grasp sequences.
args:
  arm:    { type: string, required: true, enum: [left, right] }
  action: { type: string, required: true, enum: [open, close] }
  pos:    { type: float, description: "0.0=fully closed, 1.0=fully open. Use ~0.4 for pinch pre-spread on tiny objects." }
returns:
  ok: bool
when_to_use: |
  After move_to_pose has positioned the EE — call gripper(close) at grasp
  height, gripper(open) at release height. Manual replacement for the canned
  move_to_pixel(grasp/release) sequences.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "action": "open"}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
---
