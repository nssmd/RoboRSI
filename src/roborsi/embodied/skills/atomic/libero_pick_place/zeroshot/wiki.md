# Wiki · libero_pick_place

Read-only operational guidance for Planner, Engineer, and Reviewer. Only retain
rules that are reusable across tasks and supported by visible execution
evidence.

## Stable guidance

- Preserve the exact source and destination phrases from the instruction.
- Use `find_pixel` when identity or a spatial relation is ambiguous; do not
  replace a current observation with a stored coordinate.
- Require `verify_pick_complete` before transport. A failed gate should trigger
  a new observation and a localized repair, not an unchanged grasp retry.
- Select the placement skill from the destination relation:
  `place_on_surface`, `place_object_in`, `place_beside`, or
  `place_held_at_target_servo`.
- Keep release clearance bounded and let the placement skill perform its visual
  centering. Do not substitute a guessed world coordinate.
- After release, inspect the visible result once. The agent may report
  completion, while the final simulator predicate remains post-episode.

## Failure attribution

- Wrong object: repair source identity or source-pixel selection.
- Empty grasp: repair localization, grasp candidate selection, or close height.
- Lost object during transport: repair the hold gate or motion path.
- Missed destination: repair destination identity, placement routing, or
  release clearance.
- Unclear final state: improve the final observation; do not infer success from
  the command sequence alone.
