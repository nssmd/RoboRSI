---
name: grasp_object
kind: base
robot: libero
category: control
version: 0.1.0
description: Pick a named object by perception — open, hover, descend top-down, close, and lift. Reports grasped=true only when the shared gripper classifier confirms a held state after lift.
args:
  object:         { type: string, required: true, description: "Object name from the task instruction or current camera view, e.g. alphabet soup can." }
  pixel:          { type: list, description: "Exact object pixel [u,v] from the current head image. Preferred for ambiguous instances or relational descriptions." }
  hover:          { type: float, description: "Hover / lift height above the object (m, default 0.10)." }
  grasp_z_offset: { type: float, description: "Grip-site height above the object center at close time (m, default 0.0). Raise if the gripper pushes the object instead of straddling it." }
returns:
  ok: bool
  grasped: bool
  grasp_point: list
  grasp_pixel: list
  gripper_gap: float
  gripper_state: string
  holding: bool
  visual_verified: bool
  identity_verified: bool
  do_not_regrasp: bool
  requested_object: string
  held_object: string
  requested_matches_held: bool
  source_patch_mad: float
  visual_hold_recorded: bool
when_to_use: |
  Any pick action. It grounds by vision from the object prompt and pixel, so just
  pass a visually meaningful name. If grasped=false, inspect the current image,
  then inspect holding and do_not_regrasp. Retry with a refined pixel or
  grasp_z_offset only when holding=false. If do_not_regrasp=true, never call
  grasp_object again and never open the gripper.
---

# grasp_object

Composite top-down pick: open → hover → descend → close → lift, with a
proprioceptive gripper-state confirmation (`held`) and a pure-vision check that
the source patch changed after the lift. A successful result records visual hold
evidence for fail-closed placement tools.

`holding=true` confirms only a physical hold. Treat the held object as the
requested named product only when `identity_verified=true`. If physical hold is
true but identity is unverified, do not re-grasp or open the gripper; inspect the
current image and keep the identity unknown.
