---
name: place_beside
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Set a HELD object down on the table BESIDE a perceived target, keeping it UPRIGHT. Unlike place_object_in (top-down drop INTO a container), this preserves the current grasp orientation so a side-grasped standing object (can/bottle) stays vertical, and offsets laterally so it lands next to the target, not on it. The target comes from the head camera and the held object from gripper proprioception, without task-state access.
args:
  arm:         { type: string, required: true, enum: [left, right] }
  target:      { type: string, required: true, description: "natural-language name of the reference object to place BESIDE (e.g. 'pot')" }
  held_object: { type: string, required: true, description: "natural-language name of the object currently in the gripper (e.g. 'can')" }
  offset_m:    { type: float, default: 0.08, description: "lateral gap from the target centroid (m); placed on the arm's side" }
  drop_height_m: { type: float, default: 0.03, description: "clearance above the target's surface before release; physics settles the object onto the table" }
returns:
  ok: bool
  released: bool
  target_xyz: list
  place_pt: list
  reason: str
when_to_use: |
  After grasp_object/grasp_diverse succeeds and is_holding=true, when the goal
  is to move the held object NEXT TO a reference (not into a container) and set
  it down standing — e.g. move_can_pot ("move the can beside the pot"). The VLM
  only names the target and the held object; this tool perceives both, keeps the
  grasp orientation (so the object stays upright), places to the side, and
  releases. For dropping INTO a bowl/bin/cup, use place_object_in instead.
metadata:
  tags: [base, control, sim, robotwin, placement]
  harness:
    sim_task: move_can_pot
    seeds: [1]
    args:
      - {"arm": "right", "target": "pot", "held_object": "can"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---

# place_beside · RoboTwin

## Overview
Sets a held object down on the table beside a perceived reference object,
keeping it upright. The complement of `place_object_in`: that one re-orients
top-down and drops into a container; this one keeps the grasp orientation and
places to the side, so a standing can/bottle stays standing.

## Prerequisites
- The gripper is holding the object (`is_holding` true).
- Both the reference `target` and the `held_object` are visible to the head
  camera (the held object is disambiguated as the detection nearest the gripper).

## Phases
1. Confirm holding (proprioceptive `is_holding`).
2. Perceive `target` and the held object → world positions (detect + unproject).
3. Compute the rigid gripper→object offset, then a place point offset laterally
   from the target on the arm's side, `drop_height_m` above its surface.
4. Move the flange there keeping the current grasp orientation (object stays
   upright), release, let physics settle the object onto the table.

## Success criteria
- `released` true (gripper no longer holding) and the object rests on the table
  beside the target, upright. The final episode verdict is recorded separately
  by the harness.

## Failure modes
- Perception miss (target or held object not detected) → `ok=False`, reason.
- Place point unreachable (IK) for the current arm → `ok=False`; try the other
  arm or a smaller `offset_m`.
- Object released while not actually holding → guarded by the up-front
  `is_holding` check.
