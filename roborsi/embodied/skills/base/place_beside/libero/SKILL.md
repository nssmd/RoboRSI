---
name: place_beside
kind: base
robot: libero
category: control
version: 0.1.0
description: Set the currently-held object down on the surface BESIDE a reference object/point, keeping the current grasp orientation (upright grasps stay upright). Unlike place_object_in (drops top-down INTO a container region), this offsets laterally from the reference so the item lands NEXT TO it, not on it. Pure-vision reference localization + single-arm servo.
args:
  target:      { type: string, description: "Reference object/container name to place beside. Provide this OR pos." }
  pos:         { type: list, description: "Explicit reference point [x, y, z] in world meters. Provide this OR target." }
  side:        { type: string, default: right, enum: [left, right, front, back], description: "Which side of the reference to place on. Robot frame: base is at -x, so front=+x (away from robot), back=-x (toward robot), left=+y, right=-y." }
  gap:         { type: float, default: 0.12, description: "Lateral distance from the reference center along `side` (m)." }
  dx:          { type: float, default: 0.0, description: "Extra world-frame X offset added to the place point (m); override for exact placement." }
  dy:          { type: float, default: 0.0, description: "Extra world-frame Y offset (m)." }
  dz:          { type: float, default: 0.0, description: "Extra world-frame Z offset (m)." }
  drop_height: { type: float, default: 0.04, description: "Release clearance above the reference surface (m)." }
  hover:       { type: float, default: 0.12, description: "Approach/retract height above the release point (m)." }
returns:
  ok: bool
  released: bool
  placed_object: str
  place_pt: list
  ee_pos: list
when_to_use: |
  After a grasp succeeds (is_holding / verify_pick_complete true), when the goal
  is to set the held object down NEXT TO a reference rather than into a container
  — e.g. "put the pudding to the right of the plate". Name the reference and a
  side, or pass an explicit pos. For dropping INTO a basket/bowl/drawer, use
  place_object_in instead.
metadata:
  tags: [single-arm, libero, placement, control]
---

# place_beside · LIBERO

Set a held object on the surface beside a reference, keeping the current
end-effector orientation.

## How it works
1. Confirm something is held (an object within ~5 cm of the end-effector).
2. Resolve the reference point by perception (`target`) or explicit `pos`.
3. Compute a place point offset laterally by `gap` along `side` (plus any
   explicit dx/dy/dz), `drop_height` above the reference surface.
4. Servo above it → descend (orientation kept) → open → retract.

## Success
`released` true (nothing left at the fingertips) and the object rests beside the
reference. If it is still held, the descent was blocked — raise `hover` or move
the place point.
