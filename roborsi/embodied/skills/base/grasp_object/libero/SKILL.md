---
name: grasp_object
kind: base
robot: libero
category: control
version: 0.1.0
description: Pick a named object by localizing it from the current camera/depth observation, then hovering, descending, closing, and lifting.
args:
  object:         { type: string, required: true, description: "Concrete visual object phrase, e.g. 'alphabet soup can'." }
  pixel:          { type: list, description: "Optional [u, v] returned by find_pixel. Prefer this to avoid re-localizing or switching instances." }
  hover:          { type: float, description: "Hover / lift height above the object (m, default 0.10)." }
  grasp_z_offset: { type: float, description: "Grip-site height above the object center at close time (m, default 0.0). Raise if the gripper pushes the object instead of straddling it." }
returns:
  ok: bool
  grasped: bool
  object_z_delta: float
when_to_use: |
  Any pick action. It grounds the object from the current camera frame. If
  grasped=false, retry with a more specific phrase. Pass the pixel returned by
  find_pixel whenever available.
---

# grasp_object

Camera-grounded composite pick: localize → open → hover → descend → close →
lift, with proprioceptive and visual confirmation.
