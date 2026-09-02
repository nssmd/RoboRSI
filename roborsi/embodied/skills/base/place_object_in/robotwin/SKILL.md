---
name: place_object_in
kind: base
robot: robotwin
category: control
version: 0.1.0
description: End-to-end placement atomic. Move held object above target container/zone and release. Mirrors grasp_object — handles perception + reachability + execution + verification of release.
args:
  arm:           { type: string, required: true, enum: [left, right] }
  target:        { type: string, required: true, description: "natural-language name of the destination (e.g. 'cup', 'left bowl', 'green plate')" }
  drop_height_m: { type: float, default: 0.12, description: "fingertip clearance above target's centroid before opening (m). Larger if target has tall walls." }
  retreat_m:     { type: float, default: 0.10, description: "post-release lift to clear target before VLM looks again" }
returns:
  ok: bool
  attempts: list
  reason: str
  released: bool
when_to_use: |
  USE THIS after grasp_object succeeds. The VLM only decides WHICH arm
  and WHAT target zone — this tool handles aim, descend, release, retreat,
  and verifies the gripper opened.

  WORKFLOW VS MANUAL:
    Manual (5-7 turns, often fails):
      tgt = find_pixel(object='cup')
      xyz = unproject_pixel('head_camera', tgt['u'], tgt['v'])
      move_to_pixel(arm, tgt['u'], tgt['v'], action='release', height_above_m=0.10)
      # may not align; cup is small → pen lands beside it
    place_object_in (1 turn):
      r = place_object_in(arm='right', target='cup')
      # ok=True iff released cleanly

  After place_object_in, the gripper is open and at retreat height. Call
  verify_holding_visual(arm, object='last_grasped') with lift_first=False
  if you want to confirm the dropped object is actually inside the target;
  otherwise look() and decide visually.
metadata:
  harness:
    skip_harness: true
    skip_reason: "audit v9: target_2 randomized per seed may cross midline; place trajectory IK depends on seed. Tested transitively in LH via handover_block_bicoord.place_block_in_bowl_bicoord."
---

# place_object_in · RoboTwin

End-to-end placement complement to grasp_object. Solves the symmetric
problem: gripper is holding object X, drop it INTO container Y, verify
release.

## Algorithm

1. find_pixel(target, location='center') → (u_t, v_t)
2. unproject_pixel(head_camera, u_t, v_t) → target_xyz
3. is_reachable(arm, target_xyz + drop_height * world_z, top-down quat)
4. If not reachable, return ok=False with diagnostic
5. move_fingertip_to(arm, target_xyz.x, target_xyz.y, target_xyz.z + drop_height, top-down)
6. gripper(arm, 'open')
7. is_holding(arm) → expect False (val=1.0). If still holding, retry one more
   open command + small wiggle.
8. move_fingertip_to(arm, retreat) — lift to retreat_m above target
9. Return ok=True iff is_holding=False at retreat.

The fingertip-target hover is computed via the calibrated FINGER_OFFSET
inside move_fingertip_to, so callers don't have to remember the 0.18m
offset.
