---
name: move_to_pose
kind: base
robot: libero
category: control
version: 0.1.0
description: Servo the end-effector to a world-frame target pose using OSC deltas (closed-loop, iterates internally). Optionally set orientation and hold the gripper open/closed during the move.
args:
  pos:     { type: list, required: true, description: "Target [x, y, z] in world meters." }
  quat:    { type: list, description: "Optional target orientation [x, y, z, w]. Omit to keep current orientation (top-down)." }
  gripper: { type: string, enum: [open, close, keep], description: "Gripper state to hold while moving (default keep)." }
  max_iters: { type: int, description: "Servo step cap (default 80)." }
returns:
  ok: bool
  reached: bool
  ee_pos: list
when_to_use: |
  To move the end-effector to a computed coordinate (hover point, approach, drop)
  when the composite grasp_object / place_object_in don't fit. Call ONCE with the
  final target — it iterates OSC steps for you; do not hand-step toward it.
---

# move_to_pose

Closed-loop OSC servo of the end-effector to a world pose.
