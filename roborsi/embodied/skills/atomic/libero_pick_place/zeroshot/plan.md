# Plan: libero_pick_place

## Goal
Pick the seed=0 source object off the table and place it into/onto the
designated target zone/container, then release.

## Sub-goals
1. `look` from head camera; identify the SOURCE object and the TARGET
   zone/container from the image.
2. `localize_object_top_center` on the source object for a clean world XYZ.
3. `grasp_object(arm, object=<source object>)` — end-to-end vision grasp.
4. `verify_holding_visual(arm)` — confirm real lift; if ok=False, retry
   `grasp_object` once, then re-verify.
5. `localize_object_top_center` on the target zone/container.
6. `place_object_in(arm, target=<target zone/container>)` — carry the held
   object over the target and release (open gripper).

## Success criteria
- Source object no longer at its original table location.
- Source object now resting inside/on the target zone or container.
- Gripper open (released) after the final `place_object_in` step.

## Candidate skills
- `look` — capture scene to identify source object and target visually.
- `localize_object_top_center` — world XYZ for source and target from camera.
- `grasp_object` — the dedicated vision grasp primitive (only grasp allowed).
- `verify_holding_visual` — VLM gate that the pick truly succeeded before transport.
- `place_object_in` — end-to-end carry + release over the target (no IK thrash).

## Expected n_steps
8

## Risks
- Mis-identifying source vs target — confirm both from the `look` image first.
- `grasp_object` closing on air — gate with `verify_holding_visual`, retry once.
- Tall cans can report grasped=false while ok=True — trust the visual verify.
- Never transport with the holding grasp-quat via move_to_pose (IK thrash);
  use `place_object_in`.
- Off-center/high release misses target — trust place_object_in servo centering.
- Avoid repeated find_pixel/zoom loops that burn the step budget.