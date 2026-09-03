---
name: is_holding
kind: base
robot: libero
category: perception
version: 0.1.0
description: Estimate whether the gripper is holding an object from the Panda finger opening, distinguishing closed-empty, holding, and fully-open states.
args:
  object: { type: string, description: "Optional expected-object label, retained in the result for trace readability; it does not change the proprioceptive check." }
returns:
  ok: bool
  holding: bool
  gripper_gap: float
  gripper_state: string
when_to_use: |
  After a grasp as a proprioceptive signal before transporting, or during
  recovery. For thin objects, combine the raw gap with visible scene evidence.
---

# is_holding

Grasp-state estimate from the gripper opening. It does not read object pose.
