# Plan: libero_pick_place

## Goal
Pick the requested source object and place it at the requested destination
using only current visual observations and registered skills.

## Sub-goals
1. Call `look` from the head camera and identify the exact source and
   destination phrases from the instruction.
2. Call `find_pixel` for the source when the scene contains look-alikes or a
   spatial relation.
3. Call `grasp_object` with the exact source phrase and the current source
   pixel when available.
4. Call `verify_pick_complete`; retry only after a new observation identifies
   a concrete localization or grasp error.
5. Call `find_pixel` for the destination when it is ambiguous.
6. Route the placement by destination type:
   - exposed support: `place_on_surface`;
   - container or cavity: `place_object_in`;
   - beside relation: `place_beside`;
   - externally supplied target pose: `place_held_at_target_servo`.
7. Call `look` once after release and use `done` only when the visible result
   matches the instruction.

## Success criteria
- The intended source is visibly grasped before transport.
- The source is visibly resting at the requested destination after release.
- The gripper is open and no further corrective motion is required.

## Candidate skills
- `look` — capture scene to identify source object and target visually.
- `find_pixel` — locate the exact source or destination in the current image.
- `grasp_object` — visual localization, grasp planning, execution, and lift.
- `verify_pick_complete` — deterministic held-object and lift gate.
- `place_on_surface` — precise placement on an exposed support.
- `place_object_in` — carry and release into a container or cavity.
- `place_beside` — preserve a requested beside relation.

## Expected n_steps
6-10

## Risks
- Mis-identifying source vs target — confirm both from the `look` image first.
- `grasp_object` closing on air — gate with `verify_pick_complete`.
- Choosing the wrong placement primitive — classify the destination before
  transport.
- Off-center release — use the destination pixel and the placement skill's
  bounded visual correction.
- Avoid repeated find_pixel/zoom loops that burn the step budget.
