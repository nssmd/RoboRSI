---
name: verify_pick_complete
kind: base
robot: libero
category: perception
version: 0.1.0
description: One-call gate for "did I really pick this object?" on LIBERO. Confirms grasp from the shared gripper-state classifier (`held`) and optionally requires end-effector lift height (`min_eef_z`). Deterministic proprioceptive check; no vision/VLM call.
args:
  object:    { type: string, description: "Expected held object name (for bookkeeping). If omitted, still validates grip state." }
  min_eef_z: { type: float, description: "Optional: require end-effector z to be at least this (m) to count as lifted." }
returns:
  ok: bool
  holding: bool
  lifted: bool
  object: str
  gripper_gap: float
  gripper_state: string
  eef_z: float
  reason: str
when_to_use: |
  Immediately before done(success=True) on a pick, or after grasp_object to
  confirm grasped=true independently. Pass min_eef_z to also require the gripper
  has been raised. ok=True is the ONLY
  precondition for declaring a pick done; on ok=False, re-grasp.
metadata:
  tags: [single-arm, libero, verification, proprioception]
---

# verify_pick_complete · LIBERO

Single geometric gate confirming a pick, so a done-call can't skip verification.

## Signal
This skill uses the shared LIBERO gripper classifier from `LiberoControl`:
`open / closed_empty / held / ambiguous`, derived from gripper joint range and
rolling endpoint variance. `holding=true` only when state is confidently `held`.
`gripper_gap` is reported for debugging.

## Gates
1. **holding** — shared gripper classifier state is `held`.
2. **lifted** (optional) — end-effector z ≥ `min_eef_z`, if provided.

`ok = holding AND lifted`. On failure, `reason` says which gate failed.
