---
name: grasp_rim
kind: base
robot: robotwin
category: control
version: 0.1.0
description: EXPERIMENTAL rim-pinch grasp for THIN-WALLED OPEN CONTAINERS (cup, bowl, bin, basket) whose thin rim the other grasps close on air. Localizes the container's RIM/OPENING FRAME (reuses _perception.container_opening — the top rim band's center + rim height), picks a rim-perimeter point on the arm's side, orients the jaws to close RADIALLY across the thin wall (one jaw dips inside the mouth, one stays outside), descends to grip the wall just below the rim top, closes, lifts, verifies; retries once on the opposite rim point. NOT yet validated (thin-rim grasp is a hard ceiling; scripted baselines use model contact points we cannot). Additive — returns {ok:False} cleanly. Set ROBORSI_RIM_GRASP=0 to disable.
args:
  arm:    { type: string, required: true, enum: [left, right], description: "arm to grasp with. Pick the arm on the container's side (x>0 -> right, x<0 -> left)." }
  object: { type: string, required: true, description: "natural-language name of the open container ('empty cup', 'bowl', 'bin', 'basket')." }
  u:      { type: int, description: "target pixel column of the container CENTER (from find_pixel). REQUIRED when a same-named distractor exists." }
  v:      { type: int, description: "target pixel row of the container center. Pair with u." }
  z_min:  { type: float, description: "optional world-z floor to clip the container cloud." }
  z_max:  { type: float, description: "optional world-z ceiling to clip the container cloud." }
returns:
  ok: bool               # True iff held after the lift check
  held: bool
  grasp_xyz: list        # [x, y, grasp_z] rim-pinch fingertip target (world)
  rim_z: float           # detected rim-top height
  trace: list            # per-step trace (rim, hover, descend, close, lift, verify) for both rim points
  reason: str
when_to_use: |
  USE for a THIN-WALLED OPEN CONTAINER (cup, bowl, bin, basket) that grasp_obb /
  grasp_object / grasp_flat cannot pick — the symptom is those grasps closing on
  AIR inside the mouth (the interior is empty), or is_holding staying False after
  a top-down body grasp on an open container.

  It grasps the RIM WALL, not the (hollow) body: it localizes the opening frame,
  descends the jaws to STRADDLE the thin wall at a rim-perimeter point (inner jaw
  in the mouth, outer jaw outside), and pinches RADIALLY. For a solid regular
  object use grasp_obb; for a flat slab use grasp_flat.

  EXPERIMENTAL and NOT yet validated — thin-rim grasp is a known hard ceiling
  (scripted baselines use model contact points). Try it; if it also fails on both
  rim points, the container may need a hook/two-finger-cage modality this parallel
  jaw lacks. Set ROBORSI_RIM_GRASP=0 to disable.
metadata:
  tags: [control, grasp, rim, container, cup, bowl, bin, thin-wall, experimental, robotwin]
  harness:
    skip_harness: true
    skip_reason: "Pure-vision geometric rim-pinch for thin-walled open containers; EXPERIMENTAL, not yet /tmp-validated (thin-rim grasp is a hard ceiling — scripted baselines use model contact points unavailable to pure vision). Additive: returns {ok:False} cleanly; field-validated on place_empty_cup / stack_bowls / place_can_basket campaign runs."
---

# grasp_rim · RoboTwin (EXPERIMENTAL)

Rim-pinch grasp for **thin-walled open containers** (cup, bowl, bin, basket)
whose thin rim the body grasps close on air.

## Why this exists

A cup/bowl/bin is HOLLOW — a top-down `grasp_obb`/`grasp_flat` aims at the body
footprint, so the jaws close on the empty interior (place_empty_cup: rim-pinch
air-closes 6×, grasp_obb 5/5, grasp_flat 1/1; same on stack_bowls, dump_bin).
Scripted baselines pick these by the rim via model contact points — unavailable to pure
vision. The fix is to grasp the **rim wall** directly.

## How it works

1. `look`, then localize the **rim/opening frame** (`object_mask` →
   `object_cloud` → `filter_noise` → `container_opening`): rim center, rim height,
   in-plane half-extents.
2. Pick a **rim-perimeter point** on the arm's side (right arm → +x rim point).
3. Orient the jaws to close **radially** across the thin wall (one jaw dips
   inside the mouth, one outside), open wide, hover, `descend_tcp_to_z` to just
   below the rim top so the jaws straddle the wall.
4. `gripper` close, a small lift, then `is_holding` + `verify_holding_visual`.
5. On no-hold, retry ONCE on the OPPOSITE rim point.

## Success criteria

- `held: true` — both `is_holding` and `verify_holding_visual` confirm the grip
  after the lift.

## Failure modes

- **No rim/opening frame** (mask miss / not an open container) → `ok: False`.
- **Wall too thin / jaws slip** on both rim points → `ok: False`; the container
  may need a hook / two-finger-cage modality this parallel jaw lacks.

## Enabling

Enabled for the Engineer to try; set `ROBORSI_RIM_GRASP=0` to disable.
EXPERIMENTAL — field-validated via place_empty_cup / stack_bowls / place_can_basket
runs, not a per-run harness pass.
