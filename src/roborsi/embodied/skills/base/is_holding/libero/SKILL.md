---
name: is_holding
kind: base
robot: libero
category: perception
version: 0.1.0
description: Check whether the gripper is currently holding an object using the shared LIBERO gripper classifier (`open/closed_empty/held/ambiguous`) from proprioceptive gripper joints.
args:
  object: { type: string, description: "(optional) expected held object name. If omitted, reports whether ANY object is at the fingertips." }
returns:
  ok: bool
  holding: bool
  gripper_gap: float
  gripper_state: string
when_to_use: |
  After a grasp to confirm success before transporting, or during recovery to
  decide whether to re-grasp.
---

# is_holding

Proprioceptive grasp-state check from the shared calibrated gripper classifier.
