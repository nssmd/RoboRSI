---
name: park_arm
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Move one arm to a safe "parking" pose at the edge of the workspace while preserving its current gripper state. Use BEFORE moving the OTHER arm across the workspace (e.g. cross-midline grasp), so the parked arm + its held object don't collide with the active arm's approach path. Default park pose puts the arm at the back-corner of its half of the workspace, EE at z=1.05 with top-down quat.
args:
  arm: { type: string, required: true, enum: [left, right], description: "Which arm to park. The OTHER arm stays free to act." }
  keep_grip: { type: boolean, required: false, description: "Default true. If true, the gripper command is not changed (parked arm continues holding whatever it grips). Set false only if you want the arm to drop its current load mid-park." }
  x: { type: number, required: false, description: "Override park EE x (world). Default: -0.38 for left, +0.38 for right." }
  y: { type: number, required: false, description: "Override park EE y (world). Default: -0.40 (behind the table, away from any object on it)." }
  z: { type: number, required: false, description: "Override park EE z (world). Default: 1.05 (well above the table, no risk of brushing actors)." }
  quat: { type: array, required: false, description: "Override approach quat [qx,qy,qz,qw]. Default top-down [0.5,-0.5,0.5,0.5]." }
returns:
  ok: bool
  arm: string
  park_pose: array   # [x,y,z,qx,qy,qz,qw]
  ee_after: array
  delta_m: number
  kept_grip: bool
  reason: string     # only on failure
when_to_use: |
  When the LH plan needs the OTHER arm to traverse near or across this arm's
  current pose (typical: atomic_0 picks block with left arm → atomic_1 picks
  bowl with right arm and bowl is on the same x-side as the held block).
  Insert park_arm(arm=<the-just-finished-arm>) right after the prior
  atomic so the active arm has free space.

  Workflow:
    atomic_0 — pick_block_bicoord (left arm holds block at workspace center)
    PARK     — park_arm(arm="left")  # left retreats to (-0.38, -0.40, 1.05)
    atomic_1 — pick_bowl_bicoord (right arm now has clear corridor)
    PARK     — park_arm(arm="right")  # right retreats with bowl
    atomic_2 — place_block_in_bowl_bicoord
when_NOT_to_use: |
  - Single-atomic tasks where only one arm acts (no crossing risk).
  - Right after a grasp where the held object's center-of-mass is unstable
    and the park motion would knock it loose; in that case lift straight
    up first via the grasp skill's lift_height_m, then park.
metadata:
  harness:
    skip_harness: true
    skip_reason: "single-arm safe-pose move; correctness is geometric, not task-success-gated. Verified by hand on aloha-agilex 2026-06-11."
---

# park_arm · robotwin

Implementation in `policy.py::dispatch_runtime`. Auto-discovered by
`_dispatch` in `rollout_runtime.py` — no rollout_runtime edit required.

Reuses `_do_move_to_pose` (the same primitive every other move skill
uses), so park inherits cuRobo IK + planner.

The default park pose `(±0.38, -0.40, 1.05)` was chosen by:
- ±0.38 x: far from workspace midline (x ≈ 0) where cross-arm grasps
  enter
- -0.40 y: behind the table edge (table front y ≈ -0.15, so -0.40 is
  25 cm behind it — no risk of brushing on-table actors)
- z=1.05: well above the tallest dual-arm pose seen on aloha-agilex
  (around 0.95 when holding an object); IK reliably reachable

Gripper command is not touched unless `keep_grip=false`. The kinematic
+ drive lock from `attach_held_to_gripper` carries the held actor with
the EE during the park move.
