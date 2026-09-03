---
name: gripper
kind: base
robot: libero
category: control
version: 0.1.0
description: Open or close the LIBERO Panda gripper in place (no arm motion). Holds the command for enough sim steps for the fingers to finish moving.
args:
  state: { type: string, required: true, enum: [open, close], description: "Target gripper state." }
returns:
  ok: bool
  is_open: bool
when_to_use: |
  To grasp (close) once the fingers straddle the object, or to release (open)
  over the target. For a full pick, prefer grasp_object which composes the
  approach + close + lift.
harness:
  sim_task: libero_goal_swap/0
  seeds: [9]
  args:
    - state: open
  pass_criteria:
    kind: ok_true
    min_seeds_passing: 1
---

# gripper

Direct open/close of the single Panda gripper. No motion — pair with move_to_pose.
