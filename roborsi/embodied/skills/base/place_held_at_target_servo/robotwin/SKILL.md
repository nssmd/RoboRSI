---
name: place_held_at_target_servo
kind: base_skill
robot: robotwin
category: manipulation
version: 0.1.0
description: >
  Closed-loop visual-servo PLACE. While still holding an object at a hover,
  iteratively grounds the held object and the target with Grounded-SAM,
  unprojects both to world xy, and nudges the arm by the residual until the held
  object is within tol_m of the target — then descends and releases. Closes the
  ~4 cm open-loop placement gap that fails tight-alignment tasks (match_blocks).
args:
  arm:
    type: string
    enum: [left, right]
    required: true
    description: which arm is holding the object and performs the place
  held_object:
    type: string
    required: true
    description: concrete noun phrase for the held object Grounded-SAM can ground (e.g. 'red cube')
  target:
    type: string
    required: true
    description: concrete noun phrase for the place target (e.g. 'red sign')
  camera:
    type: string
    default: head_camera
    description: camera used to ground held object and target
  tol_m:
    type: float
    default: 0.02
    description: xy convergence tolerance in meters
  max_iters:
    type: int
    default: 5
    description: max servo iterations before giving up
  max_step_m:
    type: float
    default: 0.04
    description: per-iteration nudge clamp in meters
  release:
    type: bool
    default: true
    description: open the gripper after aligning + descending
  descend_m:
    type: float
    default: 0.04
    description: descend distance in meters once aligned
  quat:
    type: list
    description: EE orientation quaternion (defaults to tip-down)
  hover_z:
    type: float
    description: hover z height in meters (defaults to current TCP z)
metadata:
  tags: [place, closed-loop, visual-servo, precision, sim, robotwin]
  embodiments: [aloha-agilex]
  backends: [robotwin, bicoord]
  when_to_use: >
    After grasping + lifting an object to a hover, when the place must land
    within a few cm of a target (matching a block to its sign, stacking,
    inserting). Use INSTEAD of place_object_in / a bare move when the task's
    success predicate checks xy distance to a target (e.g. match_blocks needs
    <= 3 cm). Pass concrete noun phrases for held_object and target so
    Grounded-SAM can ground them.
  when_NOT_to_use: >
    For coarse "drop it in the bin" placement (place_object_in is cheaper). When
    the gripper fully occludes the held object in every camera (servo cannot
    measure it — it returns ok=False with that reason; fall back to place_object_in).
    For grasping (use pick_actor_by_contact_point / grasp_object) or for the
    z-descent itself (descend_tcp_to_z).
  harness:
    skip_harness: true
    skip_reason: >
      Pure closed-loop MOTION primitive — correctness is the measured visual xy
      error converging to <= tol_m, not a grasp_holds_actor pass (the gate's
      only kind). Same category as descend_tcp_to_z. Validated in-campaign by
      tight-alignment task success (match_blocks 3 cm predicate).
  params:
    arm:         { type: str,   required: true }
    held_object: { type: str,   required: true }
    target:      { type: str,   required: true }
    tol_m:       { type: float, default: 0.02 }
    max_iters:   { type: int,   default: 5 }
    max_step_m:  { type: float, default: 0.04 }
    release:     { type: bool,  default: true }
    descend_m:   { type: float, default: 0.04 }
    camera:      { type: str,   default: head_camera }
---

# place_held_at_target_servo

Closed-loop **visual-servo place** — closes the placement-precision gap.

## Why this exists

Open-loop placement (`place_object_in`, `pick_and_place_at_pixel`) commands one
cuRobo move to the target and releases. Because of the residual grasp offset and
cuRobo's plan undershoot, the held object lands **~4 cm** from the intended spot.
That is fine to drop something in a bin, but it **fails** tasks that require
tight image-space alignment. This skill closes that residual with repeated
visual measurements rather than relying on a hidden task threshold.

This skill is the placement analogue of `descend_tcp_to_z` (which closes
cuRobo's **z**-undershoot loop): it closes the **xy** loop in image space.

## How it works

While **still holding** the object at a hover, repeat up to `max_iters`:

1. `find_pixel(held_object)` → unproject its centroid → held world xy.
2. `find_pixel(target)` → unproject → target world xy.
3. `err = target_xy − held_xy`. If `|err| ≤ tol_m` → **descend `descend_m`
   holding the aligned xy, open the gripper, done**.
4. Else `move_fingertip_to(TCP_xy + clamp(err))` — nudge the arm so the held
   object moves toward the target — and re-measure.

The step is clamped to `max_step_m` so one bad grounding cannot fling the arm.

## Prerequisites

- The object is already **grasped and lifted** to a hover (call after the grasp).
- `held_object` and `target` are **concrete noun phrases** Grounded-SAM can find
  (`'red cube'`, `'red sign'`), not `'the thing'`.

## Success criteria

- `ok=True, aligned=True`, `final_err_m ≤ tol_m`, gripper released over the target.

## Failure modes

- **Held object occluded by the gripper** in the camera → `find_pixel` misses it
  → returns `ok=False` with that reason. Try a more specific label / `look()` /
  a side camera, or fall back to `place_object_in`.
- **Non-convergence** within `max_iters` → returns `ok=False` with the last error.
- Reads only perception + the robot's own EE pose — without task-state access; no
  set_pose / teleport / attach.
