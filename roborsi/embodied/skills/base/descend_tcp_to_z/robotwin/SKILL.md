---
name: descend_tcp_to_z
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Closed-loop residual descend — drive the fingertip TCP to ACTUALLY reach a target z, compensating cuRobo's 1-3cm z-undershoot. For grasping thin/low objects (pens, markers, shallow bowls) where the jaws otherwise close on air above the object.
args:
  arm:       { type: string, required: true, enum: [left, right] }
  target_z:  { type: float, required: true, description: "world z the fingertip TCP must actually reach (object grasp/barrel-center height)" }
  x:         { type: float, description: "world x to hold during descend (default: current TCP x)" }
  y:         { type: float, description: "world y to hold during descend (default: current TCP y)" }
  quat:      { type: list, description: "wxyz EE orientation, default top-down [0.5,-0.5,0.5,0.5]" }
  floor_z:   { type: float, description: "damage-cap floor; never descend below (default target_z-0.03)" }
  tol_m:     { type: float, description: "reached when TCP within this of target_z (default 0.006)" }
  max_iters: { type: int,   description: "max residual descends (default 8)" }
returns:
  ok: bool
  reached: bool
  tcp_z: float
  z_history: list
  reason: str
when_to_use: |
  AFTER hovering/approaching above an object and BEFORE closing the gripper,
  whenever a precise descend z matters — especially THIN or LOW objects (pens,
  flat markers, shallow bowls). cuRobo's move_fingertip_to / plan_path stops
  ~1-3cm ABOVE the commanded z, so a single descend leaves the jaws closing on
  air above a thin object. This skill measures the actual fingertip TCP z and
  re-descends (over-commanding downward) until the TCP truly reaches target_z.
  Set target_z to the object's grasp height (barrel center for a pen/cylinder).
  Pipeline: get_grasp_pose / grasp_then_lift_graspgen → hover above object →
  descend_tcp_to_z(arm, target_z=grasp_z) → close gripper → lift.
when_NOT_to_use: |
  Not for the initial long-range approach (plan/move to a hover pose first).
  Not for chunky objects where a single descend already reaches the grasp z.
metadata:
  tags: [control, descend, tcp, z-compensation, thin-object, robotwin]
  harness:
    skip_harness: true
    skip_reason: "Pure closed-loop motion primitive — correctness is the measured-TCP convergence to target_z, not a grasp_holds_actor pass. Hand-authored + bench-tested directly; validated in-campaign by thin-object (collect_pens) grasp success."
---

# descend_tcp_to_z

Closed-loop residual descend that compensates cuRobo's **z-undershoot**.

## Why this exists

cuRobo's `plan_path` / `move_fingertip_to` reliably terminates **~1–3 cm ABOVE**
the commanded z (documented in `grasp_object`). For chunky objects that slack is
harmless. For **thin / low objects** (pens, flat markers, shallow bowls) it is
fatal: the gripper jaws close on **air above** the object instead of straddling
it at grasp height. This is the dominant `collect_pens` failure.

## How it works

1. Command a `move_fingertip_to` descend to `target_z`.
2. **Measure** the actual fingertip TCP z from the robot's own EE pose.
3. If the TCP is still above `target_z` by more than `tol_m`, **over-command
   downward** by the residual error (≥ `min_step`, ≤ `max_step`) and descend
   again — never below the `floor_z` damage cap.
4. Repeat up to `max_iters` until the **measured** TCP reaches `target_z`.

Because cuRobo undershoots, commanding progressively lower z lands the TCP on the
true target. Returns `reached` + the full `z_history` so the caller can see the
convergence.

## Success criteria

- `reached: true` — measured fingertip TCP z within `tol_m` of `target_z`.

## Failure modes

- Hits `floor_z` before reaching target (object lower than the damage cap allows,
  or a bad target_z) → `reached: false`, inspect `z_history`.
- IK-infeasible descend (joint limits / collision) → `move_fingertip_to` refuses;
  `z_history` stops moving. Re-ground the target xy first.
