---
name: grasp_obb
kind: base
robot: robotwin
category: control
version: 0.1.0
description: CaP-X-style OBB top-down grasp for REGULAR objects (boxes / cubes / cylinders / short poles). Segments the object (Grounded-SAM), builds its world point cloud from the mask + depth, DBSCAN-denoises, fits an oriented bounding box (OBB), and grasps TOP-DOWN with the fingers closing across the OBB's SHORTEST horizontal extent, descending to the OBB body-center height. For regular shapes this OBB-aligned grasp is more reliable than a learned grasp net (validated: ~1.1cm localize, 2/3 grasp on cubes). Enabled by default; set ROBORSI_OBB_GRASP=0 to disable.
args:
  arm:    { type: string, required: true, enum: [left, right], description: "arm to grasp with. Pick the arm on the object's side (object x>0 -> right, x<0 -> left)." }
  object: { type: string, required: true, description: "natural-language name of the object to grasp (a concrete noun phrase: 'red block', not 'the thing')." }
  u:      { type: int, description: "target pixel column of the object CENTER (from find_pixel). REQUIRED when a same-named distractor exists, else the mask may select the wrong instance." }
  v:      { type: int, description: "target pixel row of the object center. Pair with u." }
  z_min:  { type: float, description: "optional world-z floor to clip the object cloud (drops table/background points below it)." }
  z_max:  { type: float, description: "optional world-z ceiling to clip the object cloud." }
returns:
  ok: bool               # True iff the grip is confirmed (held after lift)
  held: bool
  obb: dict              # {center: [x,y,z], extent: [ex,ey,ez]} of the fitted OBB
  grasp_xyz: list        # [x, y, z] fingertip TCP grasp target (world)
  quat_wxyz: list        # top-down grasp orientation, wxyz
  trace: list            # per-step trace (look, obb, hover, descend, close, verify)
  reason: str
when_to_use: |
  USE for REGULAR objects a top-down OBB-aligned grasp suits — boxes, cubes,
  cylinders lying/standing short, short poles/pens. The jaws close across the
  object's NARROW horizontal side (the OBB's shortest horizontal extent), and
  the TCP descends to the OBB BODY-CENTER height (not the top face) for a firm
  grip. This is the CaP-X discipline that lifts pure-vision grasp reliability
  on regular shapes above a learned grasp net.

  Distinguish from the two GraspGen strategies:
    - grasp_top_down / grasp_diverse — GraspGen 6-DoF candidates + IK precheck.
    - grasp_obb (this)               — geometric OBB-aligned top-down, no net.
  Prefer grasp_obb for clean box/cylinder shapes; fall back to grasp_diverse
  for complex / irregular shapes GraspGen handles better.

  Enabled by default (validated: ~1.1cm OBB-localization localize, 2/3 grasp on cubes).
  Set ROBORSI_OBB_GRASP=0 to disable (then it returns {ok: False} without
  touching the sim).
metadata:
  tags: [control, grasp, obb, top-down, cap-x, regular-object, robotwin]
  harness:
    skip_harness: true
    skip_reason: "Camera/depth geometric grasp; validated offline on a cube task using localization error and held-after-lift evidence, not a per-run harness pass."
---

# grasp_obb · RoboTwin

CaP-X-style **OBB top-down grasp** for **regular objects** (boxes, cubes,
cylinders, short poles). One call runs the whole camera/depth pipeline without
reading task state.

## Why this exists

For regular shapes, a learned grasp net can place the jaws off-axis or on the
front shell. CaP-X's proven discipline is simpler and more reliable: fit an
**oriented bounding box** to the object's point cloud and grasp **top-down**
with the fingers closing across the box's **narrow horizontal side**, descending
to the **body center** height (not the top face — descend INTO the body).

## How it works

1. `look` (refresh head_camera).
2. **object_mask** — Grounded-SAM mask of the named object. A mask covering
   >40% of the frame is REJECTED (CaP-X lesson: SAM's top mask is often the
   whole table).
3. **object_cloud** — unproject the masked depth to a world-frame cloud (reuses
   `graspgen_infer.predict_grasps_with_mask`'s depth→world math).
4. **filter_noise** — DBSCAN(eps=0.005, min_samples=10), keep the largest
   cluster (mirrors CaP-X `filter_noise`, plus largest-cluster to shed mask
   bleed).
5. **object_obb** — open3d `OrientedBoundingBox` (center, extent, R).
6. **topdown_grasp_from_obb** — yaw from the OBB's R (fingers close across the
   shortest horizontal extent), composed with the base top-down quat
   `[0.5, -0.5, 0.5, 0.5]`; TCP z = OBB body-center height.
7. hover above → `descend_tcp_to_z` to the grasp z → `gripper` close →
   `is_holding` + `verify_holding_visual`. If not held, retry ONCE ~1.5 cm
   lower (descend further into the body).

## Success criteria

- `held: true` — both `is_holding` (finger proprioception) and
  `verify_holding_visual` confirm the grip after the lift.

## Failure modes

- **No / whole-scene mask** → `ok: False`, reason names the segmentation miss.
  Pass a more concrete `object` noun phrase, or `u,v` to disambiguate.
- **Too few depth points** (object occluded / out of the head-camera view) →
  `ok: False`. Try `scan_wrist` first or a different camera-visible pose.
- **Grip missed** even after the lower retry → `ok: False`; the object may be
  irregular (use `grasp_diverse`) or the OBB axis was wrong (bad mask).

## Enabling

Enabled by default (validated via `/tmp/pb/validate_obb_grasp.py`). Set
`ROBORSI_OBB_GRASP=0` to disable — it then returns
`{ok: False, reason: "disabled"}` without touching the sim.
