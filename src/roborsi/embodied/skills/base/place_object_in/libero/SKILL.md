---
name: place_object_in
kind: base
robot: libero
category: control
version: 0.1.0
description: Drop the currently-held object into a visually localized container or cavity by hovering above its opening, descending, releasing, and retracting. Do not use for a plate, stove, pad, stand, scale, or other exposed surface; use place_on_surface instead.
args:
  object:   { type: string, description: "Target container or cavity name (e.g. basket). Provide this OR pos." }
  pixel:    { type: list, description: "Exact target pixel [u,v] from visual localization. REQUIRED with object= for relational subregions such as back/front/left/right compartments, slots, or sections." }
  pos:      { type: list, description: "Absolute release position [x, y, z] only when object is omitted. Do not send [] or [0,0,0]; named object targets are localized by vision." }
  z_offset: { type: float, description: "Release height above the perceived target (m). Use 0.06 or lower; higher visual drops are clamped because they bounce or miss." }
  hover:    { type: float, description: "Approach/retract height above the release point (m, default 0.12)." }
returns:
  ok: bool
  released: bool
  ee_pos: list
when_to_use: |
  After grasp_object, to drop the held object into a container (object=basket_1)
  or at a computed coordinate (pos=[x,y,z]). For a specific compartment,
  slot, section, or directional target, first localize that exact region and
  pass pixel=[u,v] together with object=<description>; name-only relational
  placement is rejected. For a plate, stove, pad, stand, scale, or other exposed
  surface use place_on_surface so the object is set down at low clearance.
---

# place_object_in

Container/cavity place: hover (holding) → descend → open → retract.
