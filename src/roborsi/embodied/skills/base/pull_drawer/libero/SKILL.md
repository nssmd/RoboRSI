---
name: pull_drawer
kind: base
robot: libero
category: control
version: 0.1.0
description: Pure-vision drawer-handle pull from a head-camera pixel and local depth plane. Approaches
  from free space, closes on the handle, and pulls toward the robot.
args:
  object:
    type: string
    required: true
    description: Exact requested drawer-handle phrase from the task.
  pixel:
    type: list
    required: true
    description: Exact head-camera handle pixel [u, v] from find_by_pointing.
  approach:
    type: float
    default: 0.1
    description: Free-space approach distance in meters.
  pull_distance:
    type: float
    default: 0.12
    description: Requested outward pull distance in meters.
returns:
  ok: bool
  reached: bool
  pulled_distance: float
  reason: string
when_to_use: Use after find_by_pointing has localized the exact drawer handle requested by the task.
when_NOT_to_use: Do not use for doors, knobs that rotate, loose objects, or a pixel that does not clearly
  lie on the requested handle.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# Pull Drawer

Use the exact handle wording from the task. Localize it with
`find_by_pointing`, then pass that exact phrase and pixel here. The skill
estimates a vertical cabinet face from nearby camera depth, aligns a side-entry
grasp, approaches from free space, closes on the handle, and pulls outward.

Success from this tool means the bounded pull sequence executed with measured
gripper and end-effector evidence. The episode verdict remains external.
