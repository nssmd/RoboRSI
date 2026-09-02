---
name: grasp_object
kind: base
robot: robotwin
category: control
version: 0.1.0
description: End-to-end grasp atomic. find_pixel + get_grasp_pose top-K + reachability filter + try-each-candidate (hover, descend, close, verify_visual) + lift on success. One call replaces 8-10 tool calls of manual retry loop.
args:
  arm:    { type: string, required: true, enum: [left, right] }
  object: { type: string, required: true, description: "natural-language name of the object to grasp" }
  u:      { type: int, description: "target pixel column of the object CENTER (disambiguates which instance when the name matches several regions, e.g. 'can' also grounds to the pot). Pass the object's (u,v) from find_pixel / visual localization." }
  v:      { type: int, description: "target pixel row of the object center. Pair with u." }
  top_k:  { type: int, default: 30, description: "number of GraspGen candidates to generate (top 5 by IK-friendliness are prechecked)" }
  half_window_px: { type: int, default: 60, description: "passed to get_grasp_pose for point-cloud window" }
  z_min:  { type: float, description: "world Z lower bound (filter pcd to object slab)" }
  z_max:  { type: float, description: "world Z upper bound" }
  strategy: { type: string, default: diverse, enum: [top_down, diverse], description: "which candidates to IK-precheck. 'top_down' = steepest downward first (flat/short objects). 'diverse' = an approach-angle spread so reachable side/moderate grasps are tried too (tall cylinders like cans/bottles; robust default). The agent-facing grasp_top_down / grasp_diverse skills set this." }
returns:
  ok: bool
  attempts: list  # per-candidate result dicts
  succeeded_with: int  # index of the candidate that worked, or -1
  holding_visual: bool
  confidence: float
  reason: str
when_to_use: |
  USE THIS for any "pick up X" sub-task. It bundles the entire grasp
  pipeline — perception, pose generation, reachability filter, try each
  candidate, verify visually — into one call. The VLM only decides WHICH
  arm and WHAT object; the tool handles the loop.

  WORKFLOW VS MANUAL:
    Manual approach (8-12 turns):
      r = get_grasp_pose(object='pen_a', top_k=5)
      for cand in r['candidates']:
          # write 4-5 move_to_pose / gripper / verify lines
          # check reachability
          # break on success
    grasp_object (1 turn):
      r = grasp_object(arm='right', object='pen_a')
      # r['ok']=True iff grip confirmed visually

  After grasp_object succeeds, lift is already done (gripper holding object
  in mid-air at hover height). Move directly to placement.

  WHEN TO PREFER MANUAL: if you're debugging a specific candidate, want
  to inspect intermediate poses, or apply custom heuristics not covered.
metadata:
  harness:
    skip_harness: true
    skip_reason: "GraspGen produces 0 candidates for sub-3cm cubes (training data limit); covered by grasp_then_lift_graspgen on larger objects"
---

# grasp_object · RoboTwin

End-to-end grasp atomic. Combines perception (find_pixel) + grasp pose
generation (GraspGen via get_grasp_pose) + reachability filter
(is_reachable) + retry loop (move_fingertip_to per candidate, gripper
close, verify_holding_visual) + lift on success.

## Why this exists

Profiling 30-turn collect_pens runs showed VLM spent most turns writing
the same boilerplate retry loop in Python. The loop is mechanical — it
should be a single tool call. The VLM's job is high-level decisions
(which object, which arm), not implementing the grasp state machine.

## Algorithm

1. find_pixel(object, location='center') → (u, v)
2. unproject_pixel + ±2cm slab → z_min, z_max for point-cloud filter
3. get_grasp_pose(object=object, top_k=top_k, z_min, z_max)
4. (optional) filter candidates by approach-axis dot world-(-Z) > cos(60°)
5. filter candidates by is_reachable(arm, candidate.flange)
6. for each remaining candidate (sorted by score):
     a. open gripper
     b. move_fingertip_to(arm, candidate.tcp_xyz + 0.10 hover, quat=cand.quat)
     c. move_fingertip_to(arm, candidate.tcp_xyz, quat=cand.quat)
     d. gripper(arm, 'close')
     e. verify_holding_visual(arm, object) → if True: break (success)
     f. open + retreat to next candidate
7. Return ok = True iff any candidate succeeded; attempts list with
   per-candidate diagnostics for VLM to introspect on failure.
