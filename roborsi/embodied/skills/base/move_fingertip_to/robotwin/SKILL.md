---
name: move_fingertip_to
kind: base
robot: robotwin
version: 0.1.0
description: Move the gripper FINGERTIP (not flange) to a world-frame XYZ. Computes flange offset from quat so VLM doesn't have to.
metadata:
  tags: [base, motion, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "x": 0.2, "y": -0.1, "z": 1.0}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
args:
  arm:    { type: string, required: true, enum: [left, right] }
  x:      { type: float, required: true, description: "world-frame X of fingertip target (meters)" }
  y:      { type: float, required: true, description: "world-frame Y of fingertip target (meters)" }
  z:      { type: float, required: true, description: "world-frame Z of fingertip target (meters)" }
  quat:   { type: list, description: "[qx, qy, qz, qw] orientation. Defaults to top-down [0.5, -0.5, 0.5, 0.5]." }
  finger_offset: { type: float, description: "fingertip → flange distance along gripper -approach axis. Default 0.18 (aloha-agilex)." }
returns:
  ok: bool
  arm: str
  fingertip_target: list
  flange_target: list
when_to_use: |
  Whenever you have an OBJECT's world XYZ (from unproject_pixel or
  get_arm_pose.fingertip_xyz_top_down) and want the FINGERS to land on it.
  This is what you almost always want for grasping — the flange-based
  move_to_pose forces you to add 0.18m by hand and gets it wrong.

  Top-down approach (default quat): gripper z-axis points world -Z, so
  flange = (x, y, z + 0.18). For arbitrary quat, the offset is rotated
  by the quat so the fingertip ends up where you said.
---

# move_fingertip_to · RoboTwin

Like move_to_pose but the (x,y,z) you pass is where the FINGERTIP ends up.
Flange target is computed by adding `finger_offset` along the gripper's
-approach axis (rotated by `quat`). Saves the VLM from having to remember
the 0.18m offset every time it grasps something.
