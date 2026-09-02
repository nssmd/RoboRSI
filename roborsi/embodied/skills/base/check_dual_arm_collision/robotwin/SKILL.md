---
name: check_dual_arm_collision
kind: base
robot: robotwin
category: diagnostic
version: 0.1.0
description: |
  URDF-aware sphere collision check between the two arms (including
  optional attached objects). Uses the BiCoord-shipped
  collision_aloha_left.yml sphere data for both fl_* and fr_* link
  chains; cuRobo's per-arm planners are configured with
  extra_links: null, so they don't see each other — this skill closes
  that blind spot.

  Three modes:
  - `mode="current"`: check the CURRENT both-arm pose (no IK; just FK
    on whatever qpos each arm is at right now). Cheap diagnostic
    answering "are my arms touching right now?"
  - `mode="candidate_qpos"`: pass `arm` + `candidate_qpos` (target end
    qpos for that arm). Skill sets `arm` to candidate_qpos kinematically
    (no scene.step), runs the check, restores.
  - `mode="candidate_pose"`: pass `arm` + target flange `x,y,z,quat`.
    Skill calls plan_path on that arm to get the END qpos, then runs
    the check. Use this to pre-validate a candidate place / handover
    pose before issuing move_to_pose.

when_to_use: |
  - Before any dual-arm placement / handover / transfer to verify the
    target pose is collision-free.
  - During debugging when a motion ended in a "partial plan / didn't
    reach target" — likely cross-arm collision.
  - When designing a new dual-arm skill, sanity-check candidate poses.

when_NOT_to_use: |
  - Single-arm tasks (skill is a no-op).
  - Object-vs-object collision (e.g. bowl-vs-table). Use scene.get_contacts.

args:
  mode: { type: string, required: false, enum: [current, candidate_qpos, candidate_pose], description: "Default 'current'." }
  arm: { type: string, required: false, enum: [left, right], description: "The arm whose pose is hypothesized (candidate modes)." }
  container_arm: { type: string, required: false, enum: [left, right], description: "The other arm (default: opposite of `arm`)." }
  candidate_qpos: { type: array, required: false, description: "Target qpos for `arm` (length 8 for aloha)." }
  x: { type: number, required: false, description: "Flange target x (candidate_pose mode)." }
  y: { type: number, required: false }
  z: { type: number, required: false }
  quat: { type: array, required: false, description: "Flange target quat [qx,qy,qz,qw]. Default top-down [0.5,-0.5,0.5,0.5]." }
  attached_left: { type: string, required: false, enum: [none, bowl, block], description: "Object attached to left fl_link8. Default 'none'." }
  attached_right: { type: string, required: false, enum: [none, bowl, block], description: "Object attached to right fr_link8. Default 'none'." }
  clearance_threshold: { type: number, required: false, description: "Min clearance to consider collision. Default -0.005 (5mm penetration tolerance)." }

returns:
  ok: { type: boolean }
  collides: { type: boolean, description: "True if min_clearance < threshold." }
  min_clearance: { type: number, description: "Minimum (distance - sum_radii) across all cross-arm sphere pairs. Negative = penetration." }
  closest_pair: { type: array, description: "[holding_link, container_link] for the worst pair." }
  h_sphere_count: { type: integer }
  c_sphere_count: { type: integer }
  reason: { type: string, description: "Only on ok=False." }
---

# Overview

Pairwise sphere-distance check between two arms. Uses kinematic-only
FK (no physics step), so safe to call mid-execution without disturbing
sim state.

## Phases
1. Resolve mode (current vs candidate).
2. If candidate_pose: call `<arm>_plan_path` → take final qpos.
3. Snapshot both arms' qpos.
4. For candidate modes: set `arm` to candidate qpos.
5. Walk both arm articulations' links → load sphere data → transform
   centers to world frame.
6. Optionally add attached-object spheres to each side.
7. Pairwise distance check across holding-arm spheres × container-arm
   spheres.
8. Restore qpos.

## Success criteria
- `ok=True`: the check ran. `collides` is the real answer.

## Failure modes
- `ok=False`: candidate_pose plan_path failed; candidate_qpos shape
  mismatch; entity missing.
