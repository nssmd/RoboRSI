---
name: place_held_in_held_container
kind: base
robot: robotwin
category: control
version: 0.1.0
description: |
  ATOMIC place of an object held by one arm INTO a container (bowl) held
  by the other arm. The "holding arm" (carrying the object) moves; the
  "container arm" (carrying the bowl) STAYS PUT — its gripper is never
  opened, its EE is never commanded to move. The skill internally tries
  multiple top-down + tilted quats for the holding arm above the
  container's current world xy, picks the first IK-feasible one,
  descends, opens, and verifies the object dropped into the container.

  Created 2026-06-15 after V36-V40 atomic_2 (place_block_in_bowl_bicoord)
  failed 16 attempts across 4 runs with the same root cause: Engineer
  read "DO NOT MOVE RIGHT ARM" in a seed-recipe but still issued
  `move_to_pose(arm=right,...)` or `park_arm(arm=right)` during long
  exec_python iterations, dropping the held bowl every time. Encoding
  the invariant inside the skill — not in a prompt — removes the
  Engineer's ability to violate it.

when_to_use: |
  Atomic 2 of a handover task: one arm holds the object to place, the
  other arm holds the container. You just need the object dropped into
  the container.

when_NOT_to_use: |
  - Container is on the table (not held) — use single-arm drop instead.
  - Object is not yet picked — call grasp/pick skill first.
  - You want to release object somewhere other than ABOVE the container
    arm's current EE — this skill targets the container arm's xy.

args:
  arm: { type: string, required: true, enum: [left, right], description: "The HOLDING arm (carrying the object). It will move." }
  container_arm: { type: string, required: true, enum: [left, right], description: "The CONTAINER arm (carrying the bowl). It will NOT move. Must differ from arm." }
  drop_height_m: { type: number, required: false, description: "Vertical clearance above container EE's z when releasing. Default 0.06 (object falls ~6cm into bowl)." }
  object: { type: string, required: false, description: "Text description for visual verify. Default 'object'." }
  container: { type: string, required: false, description: "Text description for in-container verify. Default 'bowl'." }
returns:
  ok: { type: boolean }
  success: { type: boolean }
  holding_arm_ee_before: { type: array }
  container_arm_ee: { type: array }
  chosen_quat: { type: string }
  tried: { type: array, description: "Per-quat IK probe results." }
  reason: { type: string }
---

# Overview

Single atomic action: drop a held object into a held container without
ever touching the container arm.

## Prerequisites
- Both arms have grippers CLOSED on their respective objects.
- Container arm is at any stable pose with bowl held aloft.
- Holding arm has the object aloft (z above table).

## Phases
1. Read both arms' EE poses.
2. Compute target = (container_ee.x, container_ee.y, container_ee.z + drop_height_m).
3. For each candidate quat in [top_down, tilt_30_+x, tilt_30_-x, tilt_30_+y, tilt_30_-y, tilt_45_+x, tilt_45_-x], IK-test the holding arm at target.
4. Move holding arm to the first IK-feasible target.
5. Open holding-arm gripper.
6. Verify object xy ≈ container xy (within 8cm) AND container arm still
   has closed gripper (we did not accidentally release the container).

## Success criteria
- holding-arm gripper val ≈ 1.0 (opened).
- container-arm gripper val unchanged from pre-state (still holding bowl).
- object's xy within 8 cm of container's xy.

## Failure modes
- No IK candidate feasible (extreme bowl pose, arms collide). Returns
  ok=False with tried list — caller should reposition the container
  arm in a SEPARATE atomic before retrying.
- Container arm gripper val changed: ABORT, signals the skill
  accidentally disturbed the container (should not happen — bug).
