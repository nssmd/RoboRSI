---
name: is_holding
kind: base
robot: libero
category: perception
version: 0.1.0
description: Check whether the gripper is currently holding an object, judged by object-to-end-effector proximity (a grasped object sits at the fingertips). The gripper opening alone is unreliable on LIBERO.
args:
  object: { type: string, description: "(optional) expected held object name. If omitted, reports whether ANY object is at the fingertips." }
returns:
  ok: bool
  holding: bool
  to_eef: float
  gripper_gap: float
when_to_use: |
  After a grasp to confirm success before transporting, or during recovery to
  decide whether to re-grasp.
---

# is_holding

Grasp-state check from the gripper opening (+ optional object proximity).
