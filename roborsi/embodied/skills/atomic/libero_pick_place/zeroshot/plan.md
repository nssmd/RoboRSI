# Plan: libero_pick_place

## Goal
Pick the named source object and place it into the requested receptacle or onto
the requested exposed support, then release it.

## Sub-goals
1. `look` from head camera; identify the SOURCE object and the TARGET
   zone/container from the image.
2. `find_pixel(object=<source>)` and `find_pixel(object=<target>)`; retain the
   visible source and target pixels for this episode.
3. `grasp_object(object=<source>, pixel=[u, v])` — end-to-end vision grasp.
4. `verify_pick_complete(object=<source>)` — confirm real lift; if ok=False, retry
   `grasp_object` once, then re-verify.
5. Refresh the target with `find_pixel` after the grasp.
6. Follow the current instruction's relation:
   - `place_object_in(object=<target>, z_offset=0.06)` for in/inside/into a
     receptacle. For an ordinary target, pass its name without a stale
     pre-grasp pixel so the skill can clear the view and re-localize before
     transport.
   - `place_on_surface(target=<target>)` for on/onto/on top of a plate, stove,
     pad, stand, scale, shelf, table, or other exposed support.

## Success criteria
- Source object no longer at its original table location.
- Source object now resting inside/on the target zone or container.
- Gripper open after the relation-appropriate placement skill releases.

## Candidate skills
- `look` — capture scene to identify source object and target visually.
- `find_pixel` — source and target localization from the current camera image.
- `grasp_object` — the dedicated vision grasp primitive (only grasp allowed).
- `verify_pick_complete` — visible hold gate before transport.
- `place_object_in` — carry and release into a receptacle or cavity.
- `place_on_surface` — carry and release onto an exposed support.

## Expected n_steps
8

## Risks
- Mis-identifying source vs target — confirm both from the `look` image first.
- `grasp_object` closing on air — gate with `verify_holding_visual`, retry once.
- A tool-level `ok=True` is not enough; require its explicit hold evidence.
- Never transport with the holding grasp-quat via move_to_pose (IK thrash);
  use the relation-appropriate placement skill.
- Do not carry a plate-specific or basket-specific route over from another
  LIBERO task; the current runtime instruction is authoritative.
- Avoid repeated find_pixel/zoom loops that burn the step budget.
