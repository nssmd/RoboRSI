---
name: find_object_via_wrist
kind: base
robot: robotwin
category: active_perception
version: 0.1.0
description: Active-perception two-step localization. Hover a wrist camera above the coarse head-camera pixel, capture a close-up wrist frame, run VLM grounding on the zoomed view, and unproject to a PRECISE world xyz. Use when 320x240 head-camera resolution is too coarse (small objects, rims, objects inside bowls).
args:
  arm: { type: string, required: true, enum: [left, right], description: "Which arm's wrist camera to use for the close-up. Pick the arm that is NOT currently holding anything." }
  object: { type: string, required: true, description: "Natural-language description of the object to refine, e.g. 'red cube inside left silver bowl' or 'rim of the right silver bowl'." }
  location: { type: string, required: false, description: "Optional sub-location, e.g. 'graspable rim edge', 'top center'. Default: 'most graspable point'." }
  hover_height_m: { type: number, required: false, description: "Height in meters to hover the wrist above the coarse target (default 0.30)." }
returns:
  ok: bool
  u: int               # precise pixel in the wrist image
  v: int
  xyz: [float, float, float]  # precise world xyz of the refined point
  coarse_xyz: [float, float, float]
  wrist_image: string
when_to_use: |
  Call this AFTER an initial look + find_pixel + unproject_pixel pair has given
  a coarse world xyz that turned out to be too imprecise (grasp missed, VLM
  uncertain, object small/occluded). Specifically:
    - cube inside a bowl (~2cm) seen from head camera at 320x240
    - bowl rim (need to grasp the rim, not the bowl center)
    - any pre-grasp localization where prior attempts missed by 1-2cm
  This skill HOVERS the chosen wrist 30cm above the coarse point and re-grounds
  in the close-up frame, then returns a refined world xyz with much better
  accuracy. The arm you pass MUST be free (not currently holding an object).
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "object": "silver bowl", "location": "top of rim", "hover_height_m": 0.2}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---

# find_object_via_wrist · robotwin

Active-perception two-step localization to overcome 320x240 head-camera
pixel quantization (~3mm/pixel at table). Implementation in `policy.py`'s
`dispatch_runtime`. Auto-discovered by `_dispatch` in `rollout_runtime.py`.

Workflow inside the tool:
  1. snapshot head, find_pixel(object) on head → coarse (u_h,v_h)
  2. unproject(head, u_h, v_h) → coarse_xyz
  3. move_to_pose(arm, coarse_xyz.x, coarse_xyz.y, coarse_xyz.z + hover_height,
     quat=top-down) — fingertip hovers 30cm above
  4. scan_wrist(arm) → wrist image at higher effective resolution
  5. find_pixel(object) on wrist image → precise (u_w,v_w)
  6. unproject(wrist_camera, u_w, v_w) → precise xyz
  7. return precise xyz

Failure modes handled: hover IK failure (returns ok=False with reason);
wrist depth invalid (returns ok=False); VLM doesn't see object in wrist view
(returns ok=False with raw VLM response).
