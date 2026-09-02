---
name: move_dual_arm
kind: base
robot: robotwin
category: motion
version: 0.1.0
description: |
  Move BOTH arms to target flange poses in ONE synchronized motion —
  cuRobo plans the two arms together, exactly like the BiCoord expert's
  `self.move(move_to_pose(left, pose_L), move_to_pose(right, pose_R))`.

  Use this (NOT two separate move_to_pose calls) whenever the two arms
  must converge — e.g. a bowl-to-bowl tilt-pour where the left bowl is
  brought above and tilted over the right bowl. Moving one arm at a time
  makes the arms jam at the elbow on convergence (the first arm + its
  held bowl blocks the second), so single-arm move_to_pose returns
  partial plans and the bowls never dock.

  You choose both target poses yourself. For a pour, set the LEFT bowl's
  quat to a tilted (mouth-down) orientation so the dock and the pour
  happen together in this one synchronized move; keep the RIGHT bowl
  level as the catcher just below/beside it.
when_to_use: |
  - Dual-arm convergence: bringing two held objects together where
    moving one arm at a time fails (the other arm/object blocks it).
  - Specifically the handover tilt-pour dock step.
when_NOT_to_use: |
  - Single-arm motion — use move_to_pose.
  - Before grasping (nothing to converge yet).
args:
  left_pose: { type: array, required: true, description: "Left flange target [x,y,z,qw,qx,qy,qz] (7 numbers). For a pour, give the left bowl a tilted mouth-down quat." }
  right_pose: { type: array, required: true, description: "Right flange target [x,y,z,qw,qx,qy,qz] (7 numbers). Keep the right (catcher) bowl roughly level." }
returns:
  ok: { type: boolean, description: "True iff BOTH arms reached their targets in the synchronized move." }
  plan_success: { type: boolean, description: "False if cuRobo refused the joint motion (collision / unreachable)." }
  left_reached: { type: boolean }
  right_reached: { type: boolean }
  left_ee_after: { type: array }
  right_ee_after: { type: array }
  note: { type: string }
---

# Overview

One synchronized dual-arm move. Exposes the BiCoord `impl.move((left,
[move]), (right,[move]))` primitive so two arms converge together
without the single-arm elbow-jam that blocks a tilt-pour dock.

## Phases
1. Parse both 7-vector flange targets `[x,y,z,qw,qx,qy,qz]`.
2. Snapshot both EE poses (before).
3. Issue ONE `impl.move((left,[move]),(right,[move]))` — cuRobo plans
   both arms jointly.
4. Read both EE poses (after); report reached / moved / plan_success.

## Success criteria
- `ok=True`: BOTH arms reached their targets (plan_success and each EE
  within 3cm of its target).

## Failure modes
- `ok=False` with `plan_success=False`: cuRobo refused the joint motion
  (cross-arm collision or unreachable IK) — try poses higher up / farther
  apart, or pre-check each with check_dual_arm_collision / is_reachable.
- `ok=False` with reached=False: partial plan; the arms moved but didn't
  arrive.
