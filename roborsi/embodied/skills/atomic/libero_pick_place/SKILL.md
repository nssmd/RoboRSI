---
name: libero_pick_place
kind: atomic
domain: manipulation
version: 0.1.0
description: Pick a named object and place it in/on its target container on the single-arm LIBERO backend using camera grounding and OSC servo control.
metadata:
  tags: [single-arm, pick-place, libero, sim]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  libero_task: libero_object/0
  vlm_prompts:
    instruction: |
      You control a single Franka Panda arm in a LIBERO tabletop scene. Parse
      the source and target from the task instruction, then localize them from
      the current camera observation.

      Do this:
      1) Call look, then use find_pixel with concrete source and target names.
      2) For a pick-and-place ("put X in/on Y"):
             grasp_object(object=<item>)      # confirm grasped=true
             place_object_in(object=<target>) # localizes and drops into the target
      3) If grasp_object returns grasped=false, retry ONCE with
         a more specific object phrase or an explicit pixel from find_pixel.
      4) Once the instruction is satisfied, call done(success=True). Success is
         recorded by the harness after the episode; if stuck after honest tries,
         call done(success=False).
    expected_on_success: |
      The named object is visibly placed in/on the target and released.
  active_executor:
    default: zeroshot
    threshold: 0.70
---

# libero_pick_place (atomic)

## Scene

Single-arm Franka Panda on a LIBERO tabletop (robosuite/MuJoCo, OSC end-effector
control). Several graspable objects plus a target container. Default task
`libero_object/0` — "pick the alphabet soup and place it in the basket".

## Goal

Satisfy the LIBERO task instruction using the current camera observation. The
harness records the final simulator verdict after execution.

## How it is driven

The VLM composes the `base/libero` muscle: `look` and `find_pixel` for visual
grounding, `grasp_object` to pick, and `place_object_in` to place. Lower-level
`move_to_pose` / `gripper` are available for corrections.
