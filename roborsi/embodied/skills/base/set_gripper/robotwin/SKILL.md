---
name: set_gripper
kind: base
robot: robotwin
version: 0.1.0
description: Open or close one or both grippers on the active RoboTwin scene.
args:
  state:
    type: string
    enum: [open, close]
    required: true
    description: whether to open or close the gripper(s)
  arm:
    type: string
    enum: [left, right, both]
    default: both
    description: which gripper(s) to actuate
metadata:
  tags: [base, gripper, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "state": "open"}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
params:
  env:    { type: object, required: true }
  arm:    { type: string, default: both, description: "'left' | 'right' | 'both'" }
  state:  { type: string, required: true, description: "'open' | 'close'" }
returns:
  ok: "bool"
---

# set_gripper · RoboTwin

Open or close grippers. Wraps `together_open_gripper` / `together_close_gripper` (or the per-arm variants where available). For dual-arm setups the default is **both**, so a single call resets the gripper state for the whole rig.

## When to use

- Before / after grasping (paired with `move_to_pose`).
- Reset routines (open both at end of episode).

## Notes

- The state is **fully open** (`pos=1`) or **fully closed** (`pos=0`); fine-grained widths require `move_by_displacement` style fingertip control which is out of scope for the base layer.
