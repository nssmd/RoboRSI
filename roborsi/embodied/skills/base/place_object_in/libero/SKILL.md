---
name: place_object_in
kind: base
robot: libero
category: control
version: 0.1.0
description: Place the currently-held object at a target — either an absolute world position or above a named container/object — by hovering, descending, releasing, and retracting.
args:
  object:   { type: string, description: "Target container/object name (e.g. basket). Used to recall or perceive the target." }
  pixel:    { type: list, description: "Optional target-interior [u, v] returned by find_pixel. Prefer this when available." }
  pos:      { type: list, description: "Absolute release position [x, y, z] in world meters. Provide this OR object." }
  z_offset: { type: float, description: "When object= is used, release height above the target center (m, default 0.06)." }
  hover:    { type: float, description: "Approach/retract height above the release point (m, default 0.12)." }
returns:
  ok: bool
  released: bool
  ee_pos: list
when_to_use: |
  After grasp_object, to drop the held object into a container. Prefer
  object=basket plus pixel=[u,v] from find_pixel, or use pos=[x,y,z] when the
  release coordinate is already known.
---

# place_object_in

Composite place: hover (holding) → descend → open → retract.
