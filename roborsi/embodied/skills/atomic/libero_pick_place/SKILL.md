---
name: libero_pick_place
kind: atomic
domain: manipulation
version: 0.1.0
description: Pick a named object and place it in a receptacle or on an exposed support on the single-arm LIBERO backend using camera grounding and whole-arm servo control.
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
      2) Call grasp_object(object=<item>) and require a confirmed hold.
      3) Choose the placement skill from the CURRENT task relation:
         - "in", "inside", or "into" a receptacle/cavity such as a basket,
           bin, bowl, drawer, or container:
           place_object_in(object=<target>, z_offset=0.06).
           For an ordinary non-relational target, pass the target name without
           a stale pre-grasp pixel so the skill can clear the view and re-localize.
         - "on", "onto", or "on top of" an exposed support such as a plate,
           stove, pad, stand, scale, shelf, or table:
           place_on_surface(target=<target>)
         Never use place_object_in for a plate or other exposed support.
      4) If grasp_object returns grasped=false, retry ONCE with
         a more specific object phrase or an explicit pixel from find_pixel.
      5) Once the instruction is satisfied, call done(success=True). Success is
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

Single-arm Franka Panda on a LIBERO tabletop (robosuite/MuJoCo, whole-arm joint
control). Several graspable objects plus a target receptacle or support. Default task
`libero_object/0` — "pick the alphabet soup and place it in the basket".

## Goal

Satisfy the LIBERO task instruction using the current camera observation. The
harness records the final simulator verdict after execution.

## How it is driven

The VLM composes the `base/libero` muscle: `look` and `find_pixel` for visual
grounding, `grasp_object` to pick, `place_object_in` for receptacles, and
`place_on_surface` for exposed supports. Lower-level `move_to_pose` / `gripper`
are available for corrections.
