---
metadata:
  harness:
    sim_task: libero_goal/2
    seeds: [21, 22]
    args:
      - object: wine bottle
        hover: 0.1
        grasp_z_offset: 0.03
    pass_criteria:
      kind: grasp_holds_actor
      min_seeds_passing: 2
name: grasp_object
kind: base
robot: libero
category: control
version: 0.1.0
description: Pick a named object by perception — open, hover, descend or side-enter, close, lift, and verify the final hold.
args:
  object:         { type: string, required: true, description: "Object name from the task instruction or current camera view, e.g. alphabet soup can." }
  pixel:          { type: list, description: "Exact object pixel [u,v] from the current head image. Preferred for ambiguous instances or relational descriptions." }
  hover:          { type: float, description: "Hover / lift height above the object (m, default 0.10)." }
  grasp_z_offset: { type: float, description: "Grip-site height above the object center at close time (m, default 0.0). Raise if the gripper pushes the object instead of straddling it." }
returns:
  ok: bool
  grasped: bool
  success: bool
  holding_visual: bool
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
  Any pick action. It grounds by vision from the object prompt and pixel, so pass
  a visually meaningful name. If grasped=false, inspect the current image, then
  inspect holding and do_not_regrasp. Retry only when holding=false and the scene
  provides a fresh, stable target observation. If do_not_regrasp=true, never call
  grasp_object again and never open the gripper.
---

# grasp_object

Composite perception pick with calibrated gripper-state confirmation and a
pure-vision source-patch check after a reduced collision-aware clearing motion.
If the original grounded pixel produces no GraspGen candidate, the skill may
request one fresh detector relocalization. That replacement is used only when
it differs from the failed pixel, remains within the bounded source region,
passes marked-pixel identity verification against fresh public RGB for every
object phrase, and produces both an actual GraspGen candidate and a finite,
nonempty measured point cloud. Otherwise the skill fails without motion. This
is not a same-mask or cloud-proximity identity claim.

`success=true` and `holding_visual=true` are harness-facing aliases. They are
true only when the final post-clear gripper state is `held`, visual hold evidence
was recorded, and target identity remained verified. They are false on all
pre-motion failures. They do not fabricate simulator task success.

`holding=true` confirms only a physical hold. Treat the held object as the
requested product only when `identity_verified=true`. If physical holding is
true but identity is unverified, do not re-grasp or open the gripper. If holding
is lost during source clearing, this skill stops and reports failure rather than
automatically approaching the dropped object.
