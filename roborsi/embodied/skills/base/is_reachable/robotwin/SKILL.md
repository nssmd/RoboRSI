---
name: is_reachable
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Quick reachability predicate for an arm + target pose. Filter unreachable grasp candidates before paying the full plan/execute cost.
args:
  arm:  { type: string, required: true, enum: [left, right] }
  x:    { type: float, required: true, description: "world-frame X (flange target)" }
  y:    { type: float, required: true }
  z:    { type: float, required: true }
  quat: { type: list, description: "[qx, qy, qz, qw], default top-down [0.5,-0.5,0.5,0.5]" }
returns:
  ok: bool
  reachable: bool
  reason: str
  distance_to_base: float
when_to_use: |
  Before move_to_pose or move_fingertip_to with a pose from get_grasp_pose
  (often returns workspace-edge poses). If reachable=False, skip that
  candidate and try the next. Saves 5-10 wasted seconds per unreachable
  attempt. Cheap (no actual planning) — current heuristic is distance to
  arm base + workspace-X-side check; promote to mplib IK precheck later.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "x": 0.2, "y": -0.1, "z": 1.0}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---

# is_reachable · RoboTwin

Quick "can the arm reach this?" predicate. Used to filter grasp pose
candidates before committing to a full move_to_pose attempt.

## Heuristic (v0.1)

- Aloha-agilex left arm base ≈ (-0.18, 0, 0.85), right arm base ≈ (0.18, 0, 0.85).
- Reach radius ~0.55 m. Crossing midline reduces effective reach.
- If distance to base > 0.55, return False.
- If left arm asked to grasp at x > 0.10 (right side of midline), or right arm at x < -0.10, return False (cross-arm reach).

This is rough but catches the obvious failure modes (pen at x=0.6 commanded to right arm whose base is at x=0.18, reach ~0.42m needed but with offset → unreachable).
