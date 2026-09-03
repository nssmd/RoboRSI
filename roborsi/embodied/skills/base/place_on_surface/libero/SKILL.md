---
name: place_on_surface
kind: base
robot: libero
category: control
version: 0.1.0
description: Gently set an object ONTO an exposed surface such as a plate, stove, pad, stand, or scale. Requires visual hold evidence from the current successful grasp_object call, preserves the grasp orientation, descends to a low release pose, and never opens unless every reach and hold gate passes. Do not use for a container, basket, bin, bowl, drawer, microwave cavity, or beside relation.
args:
  target: { type: string, description: "Named exposed surface target. Provide this or pixel." }
  pixel: { type: list, description: "Exact target-surface pixel [u,v] from the current head view. Preferred for ambiguous or relational surfaces." }
  release_clearance: { type: float, default: 0.025, description: "End-effector release clearance above the perceived surface in meters; clamped to 0.01-0.05." }
  hover: { type: float, default: 0.12, description: "Approach and retract height above the release pose in meters." }
  pos_tol: { type: float, default: 0.02, description: "Maximum pre-release position error in meters." }
returns:
  ok: bool
  reached: bool
  released: bool
  gripper_opened: bool
  object_release_verified: "bool | null"
  target_pixel: list
  target_world: list
  target_source: string
  pre_release_error: float
  pre_release_z_error: float
  source_clear_verified: bool
  gripper_hold_continuity: bool
  source_clear_reason: string
  evidence_object: string
  visual_source_mad: float
  gripper_state_before: string
  gripper_state_pre_release: string
  gripper_state_after: string
  ee_pos: list
when_to_use: |
  Use after grasp_object succeeds and records visual hold evidence, when the
  destination is an exposed support surface: plate, stove burner, pad, stand,
  scale, or tabletop region. A manual gripper close or gap-only HELD reading is
  not sufficient; re-run grasp_object if the evidence is missing.
  For a basket, bin, bowl, drawer, or cavity use place_object_in. For beside
  relations use place_beside. For an exact externally-derived pose use
  place_held_at_target_servo.
---

# place_on_surface

Resolve the exposed target surface from vision, preserve the held object's
current orientation, reach a low release pose, confirm the hold again, open
only after convergence, and retract. `ok=True` reports only this tool's
motion result. `released` is a backward-compatible alias for
`gripper_opened`; `object_release_verified` remains unknown until a later visual
inspection confirms the object left the gripper and rests on the destination.
