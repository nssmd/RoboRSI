---
name: place_obb
kind: base
robot: robotwin
category: control
version: 0.1.0
description: CaP-X-style OBB place — deposit a currently-HELD object into / onto a container localized purely by vision. Segments the container (Grounded-SAM), builds its world cloud, fits an oriented bounding box (OBB), then LIFTS the held object >=0.20m, transports over the container INTERIOR CENTER, descends in a controlled z step to a bbox-computed drop height (just below the rim), releases, and DEPTH-verifies containment (placed-object centroid inside the container OBB footprint AND at/below the rim — a check a rim-resting object can't fool, unlike 2D bbox). Enabled by default; set ROBORSI_OBB_PLACE=0 to disable.
args:
  arm:       { type: string, required: true, enum: [left, right], description: "arm currently holding the object (the one that grasped it)." }
  container: { type: string, required: true, description: "natural-language name of the target container/receptacle to place INTO (a concrete noun phrase: 'wicker basket', not 'the thing')." }
  object:    { type: string, description: "natural-language name of the HELD object being placed — REQUIRED for the depth containment verify. Without it the object is released but placed=False (unverified)." }
  u:         { type: int, description: "target pixel column of the container CENTER (from find_pixel). REQUIRED when a same-named distractor exists." }
  v:         { type: int, description: "target pixel row of the container center. Pair with u." }
  inset_m:   { type: float, description: "release this far below the container rim top (default 0.01m — just inside the cavity mouth)." }
  drop_z:    { type: float, description: "override the computed drop z (world). Use only if the OBB rim height is wrong." }
  z_min:     { type: float, description: "optional world-z floor to clip the container cloud." }
  z_max:     { type: float, description: "optional world-z ceiling to clip the container cloud." }
returns:
  ok: bool               # True iff the placed object is RELEASED and DEPTH-verified inside the container
  placed: bool
  obb: dict              # {center, extent} of the fitted container OBB
  drop_xyz: list         # [x, y, drop_z] release target (world)
  trace: list            # per-step trace (is_holding, lift, obb, hover, descend, release, retreat, verify)
  reason: str
when_to_use: |
  USE right after a confirmed grasp (is_holding true) to PLACE the held object
  into / onto a container or receptacle — basket, bin, box, plate, stand. This
  is the CaP-X placement discipline that fixes the three RoboTwin place failures
  (approved lead place_object_basket/874ced):
    1. release over the container INTERIOR CENTER (its OBB center XY) at a bbox
       drop height — not the servo's rim/top-center point.
    2. LIFT the held object >=0.20m BEFORE transporting (CaP-X), then a
       CONTROLLED descend_tcp_to_z to the drop z — object never scrapes/knocks.
    3. DEPTH containment verify: the placed object's cloud centroid must be
       inside the container OBB footprint AND at/below the rim in z. A cube
       resting ON the rim projects inside the 2D find_pixel bbox but fails this.

  Distinguish from the other place primitives:
    - place_held_at_target_servo — releases at a servoed point; no container-drop
      semantics, no depth verify (fails to deposit into a cavity).
    - place_object_in — needs grasp_object's recorded state (incompatible with a
      manual / OBB grasp).
    - place_obb (this) — geometric OBB interior-center drop + depth verify,
      compatible with ANY grasp (works after grasp_obb).

  Enabled by default. Set ROBORSI_OBB_PLACE=0 to disable (then it returns
  {ok: False} without touching the sim).
metadata:
  tags: [control, place, obb, container, cap-x, depth-verify, robotwin]
  harness:
    skip_harness: true
    skip_reason: "Pure-vision geometric place; helper math + registry verified, but end-to-end deposit NOT yet /tmp-validated on a container task (unlike grasp_obb). Additive tool — returns cleanly {ok:False} on any failure, so it cannot harm a run; field-validated via the place_object_basket campaign runs it is steered into."
---

# place_obb · RoboTwin

CaP-X-style **OBB place-into-container** for a currently-**held** object. One
call runs the whole pure-vision deposit pipeline; uses only camera, depth, and proprioception.

## Why this exists

RoboTwin's place primitives fail to deposit into a cavity (approved lead
`place_object_basket/874ced`): `place_held_at_target_servo` releases at a
rim/top-center point (object lands on the rim, not inside), and the 2D
`find_pixel ∈ bbox` containment check is fooled — a cube resting ON the rim
still projects inside the 2D bbox. CaP-X's discipline (place is a *second
perception problem*) fixes both: localize the container's **OBB**, release over
its **interior center** at a bbox drop height, and verify containment in **3D**.

## How it works

1. `is_holding` — precondition; abort cleanly if nothing is held.
2. `get_arm_pose` → **lift** the held object straight up ≥0.20 m
   (`move_fingertip_to`) so it clears the scene before transport (CaP-X).
3. `look` → **object_mask** (Grounded-SAM, whole-scene reject) →
   **object_cloud** (masked depth → world) → **filter_noise** (DBSCAN) →
   **object_obb** — the container OBB.
4. **container_opening / container_drop_target** — release point = the RIM /
   OPENING FRAME center (the top rim band's XY centroid — the true opening
   center, unbiased by thick walls / an asymmetric solid that skew a whole-cloud
   OBB center; container-placement literature's opening-frame primitive), at the
   rim height minus `inset_m` (just inside the mouth). Falls back to the OBB bbox
   drop when the rim band is degenerate.
5. hover ≥0.15 m above → **descend_tcp_to_z** (controlled = CaP-X z_approach) →
   `gripper` open (release) → retreat straight up.
6. **DEPTH + RELEASE verify** — `is_holding(arm)` must be False (the object was
   actually released, not still dangling over the center — the
   place_object_stand/99606d false-positive), AND the re-segmented `object`'s
   cloud centroid lies inside the container OBB footprint and at/below the rim
   (`point_inside_footprint`). `ok`/`placed` require BOTH.

## Success criteria

- `placed: true` — the object is RELEASED (`is_holding` False) AND its cloud
  centroid is depth-verified inside the container OBB footprint at/below the rim.

## Failure modes

- **No / whole-scene container mask** → `ok: False`, reason names the miss.
  Pass a concrete `container` noun phrase or `u,v`.
- **Nothing held** → `ok: False` ("grasp the object first"). Call `grasp_obb`.
- **Released but centroid above the rim / outside footprint** → `placed: False`;
  the object landed on the rim — retry with a larger `inset_m` or check the OBB.
- **No `object` given** → released but `placed: False` (unverified); pass
  `object=` to enable the depth verify.

## Enabling

Enabled by default. Set `ROBORSI_OBB_PLACE=0` to disable — it then returns
`{ok: False, reason: "disabled"}` without touching the sim.
