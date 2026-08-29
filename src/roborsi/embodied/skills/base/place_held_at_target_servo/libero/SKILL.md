---
name: place_held_at_target_servo
kind: base
robot: libero
category: control
version: 0.1.0
description: Place the held object at an exact externally-derived pose (position plus optional orientation) with a tight position tolerance. Use only when an allowed perception or planning step already supplied that exact pose. For an exposed surface without an exact pose, use place_on_surface; for a container or cavity, use place_object_in.
args:
  pos:      { type: list, description: "Target release position [x, y, z] in world meters. Provide this OR object." }
  object:   { type: string, description: "Target object whose perceived position is the release point. Provide this OR pos." }
  quat:     { type: list, description: "Optional target end-effector orientation [qx, qy, qz, qw]; if given, the servo aligns orientation too (needed for orientation-sensitive placement)." }
  z_offset: { type: float, default: 0.0, description: "Release height above the resolved target (m)." }
  hover:    { type: float, default: 0.12, description: "Approach/retract height above the target (m)." }
  pos_tol:  { type: float, default: 0.006, description: "Position tolerance the servo must reach before releasing (m)." }
returns:
  ok: bool
  reached: bool
  target: list
  final_pos_error: float
  ee_pos: list
when_to_use: |
  After a grasp, only when an allowed external perception or planning result
  supplies an exact target position and, if needed, orientation. For ordinary
  plates, stove burners, pads, stands, scales, and other exposed supports use
  place_on_surface. For a basket, bowl, drawer, or cavity use place_object_in.
metadata:
  tags: [single-arm, libero, placement, precision, servo]
---

# place_held_at_target_servo · LIBERO

Precise closed-loop placement of a held object at an exact pose.

## How it works
1. Resolve the target (explicit `pos`, or `object` name by perception), plus
   `z_offset`.
2. Servo above the target (holding) → servo down to the target with a tight
   `pos_tol` (and align `quat` if given) → open → retract.

## Success
`reached` true means the end-effector arrived within `pos_tol` before release;
`final_pos_error` reports the achieved error. If `reached` is false the target
was likely unreachable or obstructed — check is_reachable, raise `pos_tol`, or
adjust the target.
