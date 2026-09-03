---
name: close_drawer
description: Pure-vision drawer close from a fresh drawer-front or handle pixel. Fits the cabinet face, approaches from free space, and pushes inward opposite the measured outward normal.
when_to_use: Use only when the task explicitly requires closing a visible open drawer.
when_NOT_to_use: Do not use for opening drawers, hinged doors, loose objects, or while holding an object.
args:
  object:
    type: string
    required: true
    description: Exact requested drawer front or handle phrase.
  pixel:
    type: list
    required: true
    description: Fresh head-camera drawer pixel [u, v].
  approach:
    type: float
    default: 0.09
    description: Bounded free-space standoff in meters.
  push_distance:
    type: float
    default: 0.18
    description: Bounded inward push distance in meters.
harness:
  sim_task: libero_90/22
  args:
    - object: bottom drawer handle of the cabinet
      pixel: [286, 325]
      approach: 0.09
      push_distance: 0.18
  pass_criteria:
    kind: ok_true
    min_seeds_passing: 1
---

# Close Drawer

Use a fresh visual drawer pixel. The tool derives the cabinet face normal from
RGB-D, pushes inward, and reports measured motion. Native task success remains
post-hoc and is never exposed by this tool.
