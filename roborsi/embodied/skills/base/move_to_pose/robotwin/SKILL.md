---
name: move_to_pose
kind: base
robot: robotwin
version: 0.1.0
description: |
  LOW-LEVEL flange-pose command. Most users want move_fingertip_to instead!
  The `z` you pass is the EE FLANGE's world Z, NOT the fingertip's.
  Fingertip ≈ z - 0.18m for the default top-down quat. If you have an
  object's world XYZ from unproject_pixel and want fingers ON that object,
  call move_fingertip_to(x, y, z) — it handles the offset for you and won't
  drive the fingertips through the table.
metadata:
  tags: [base, motion, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "x": 0.2, "y": -0.1, "z": 1.0, "quat": [0.5, -0.5, 0.5, 0.5]}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
args:
  arm:  { type: string, required: true, enum: [left, right] }
  x:    { type: float, required: true, description: "world-frame X (meters)" }
  y:    { type: float, required: true, description: "world-frame Y (meters)" }
  z:    { type: float, required: true, description: "world-frame Z of EE flange (meters). Fingertip ≈ z - 0.18 for top-down quat." }
  quat: { type: list, description: "[qx, qy, qz, qw] orientation. Defaults to top-down [0.5, -0.5, 0.5, 0.5]." }
returns:
  ok: bool
  arm: str
  target_pose: list
when_to_use: |
  Use ONLY when you need a pose that ISN'T tied to "fingers at object XYZ" —
  e.g., parking the arm at a known waypoint, lifting STRAIGHT UP after a
  grasp by a fixed amount, or composing a tilt-pour with hand-tuned z values.

  For ANY pick / press / drop targeting an object whose world XYZ you got
  from unproject_pixel, get_arm_pose, find_object_via_wrist, or get_grasp_pose:
  USE move_fingertip_to (or grasp_then_lift). Calling move_to_pose with
  object_z as the z parameter will drive the fingertips ~18cm BELOW the
  object — usually into / through the table — and IK will refuse with ok=False.
---

# move_to_pose · RoboTwin

Drive the chosen arm's EE flange to a world-frame pose. Uses RoboTwin's
`mplib`-backed planner; will refuse with `ok=False` if no collision-free path
is found. Side-effect free wrt gripper state.
