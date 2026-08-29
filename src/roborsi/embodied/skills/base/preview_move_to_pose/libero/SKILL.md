---
name: preview_move_to_pose
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Plan a branch-continuous JOINT_POSITION trajectory to a world-frame Panda target and render the target in fresh orbit views without moving the robot.
args:
  pos: { type: list, required: true, description: "Target world [x,y,z] meters." }
  quat: { type: list, description: "Optional [x,y,z,w] orientation." }
  gripper: { type: string, default: keep, enum: [open, close, keep] }
  max_iters: { type: int, default: 80 }
returns:
  ok: bool
  reachable: bool
  preview_id: string
when_to_use: |
  Before a precise manual move or any close command. Inspect the green target
  marker, then pass the one-time preview_id to execute_previewed_move.
when_NOT_to_use: |
  A preview proves a bounded planned path, not physical contact or task success.
  Any intervening observation or world action invalidates the token.
metadata:
  harness:
    skip_harness: true
    skip_reason: "requires live LIBERO IK and orbit rendering"
---

# preview_move_to_pose

Read-only trajectory and visual target preview. No simulator predicate is queried.
