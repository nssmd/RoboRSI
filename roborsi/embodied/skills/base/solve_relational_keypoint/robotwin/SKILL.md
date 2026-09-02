---
name: solve_relational_keypoint
kind: base
robot: robotwin
category: control
version: 0.1.0
description: |
  ReKep-style relational keypoint move: ground a keypoint on the HELD
  object and a keypoint on a TARGET object, lift both to 3D, and compute
  the end-effector displacement that brings the held keypoint onto the
  target keypoint (+offset). dry_run by default — returns the target pose
  without moving. Source: ReKep (Relational Keypoint Constraints, Huang et
  al. 2024).
args:
  arm:             { type: string, required: true, enum: [left, right], description: "Arm holding the moving object." }
  moving_keypoint: { type: string, required: true, description: "Keypoint on the held object (e.g. 'the pouring edge of the held bowl')." }
  target_object:   { type: string, required: false, description: "Target object noun phrase to anchor grounding (e.g. 'the right bowl')." }
  target_keypoint: { type: string, required: true, description: "Keypoint on the target (e.g. 'the center of the right bowl')." }
  offset_xyz:      { type: array,  required: false, description: "World offset added to the target keypoint (default [0, 0, 0.02])." }
  dry_run:         { type: bool,   required: false, description: "If true (DEFAULT), compute target pose only, do not move." }
returns:
  ok: bool
  dry_run: bool
  moving_keypoint_xyz: array (xyz)
  target_keypoint_xyz: array (xyz)
  goal_xyz: array (xyz)
  delta_xyz: array (xyz, displacement to apply)
  target_ee_pose: array (xyz + quat_wxyz, 7 floats)
  reason: string
when_to_use: |
  When a manipulation goal is naturally a constraint between a point on
  the held object and a point on a target — aligning a pour edge over a
  bowl, a peg over a hole, a tool tip over a contact site. Run with
  dry_run=True first to inspect delta_xyz, then dry_run=False to execute.
when_NOT_to_use: |
  When the goal needs ROTATIONAL alignment (tilt the pour edge down) — this
  skill translates only (orientation kept). Pair with a separate
  rotate/pour primitive. Also not a grasp — the object must already be held.
metadata:
  harness:
    skip_harness: true
    skip_reason: "VLM-grounding + geometry skill (find_pixel in the loop, dry_run by default) — not run through the deterministic auto-harness."
---

# solve_relational_keypoint (ReKep)

Encodes a goal as a relational constraint between a keypoint on the held
object and a keypoint on a target object. Grounds both with the VLM
(`find_pixel` → `unproject_pixel`), computes
`delta = (target_kp + offset) - moving_kp`, and applies it to the holding
arm's end-effector (translation only; orientation unchanged).

## Usage
- `arm`: the arm holding the moving object.
- `moving_keypoint`: phrase for the held-object keypoint.
- `target_object` / `target_keypoint`: the target and its keypoint.
- `offset_xyz`: world offset added to the target keypoint (default
  `[0, 0, 0.02]` — 2 cm above).
- `dry_run`: default **True** — returns `delta_xyz` + `target_ee_pose`
  without moving. Set False (or feed `target_ee_pose` to `move_to_pose`)
  to execute.

## Failure modes
- Either keypoint fails to ground/unproject → `ok=False` with the
  offending keypoint + reason.
- Single-camera unproject + VLM jitter make the geometry approximate —
  inspect `delta_xyz` under dry_run before committing.
- Translation-only: a goal needing pour-tilt rotation is NOT solved here
  (see the ReKep-rotation TODO in policy.py).
- `target_ee_pose` may be IK-unreachable → on dry_run=False the
  `move_to_pose` plan is refused and `ok=False` with `move_note`.
