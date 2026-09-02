---
name: move_to_pixel
kind: base
robot: robotwin
version: 0.1.0
description: High-level pixel-targeted action. Internally unprojects (u,v) to world XYZ and synthesizes a complete action sequence (open/descend/close/lift for grasp; descend/open/lift for release; etc.).
args:
  arm:            { type: string, required: true, enum: [left, right] }
  u:              { type: int,    required: true }
  v:              { type: int,    required: true }
  action:         { type: string, required: true, enum: [hover, grasp, pinch_grasp, release, tap] }
  height_above_m: { type: float,  default: 0.0 }
  camera:         { type: string, default: head_camera }
returns:
  ok: bool
  reason: str
  ee_xyz: list
when_to_use: |
  Default high-level grasp/release. action="grasp" runs open→hover→descend
  →close→lift with calibrated FINGER_OFFSET=0.18 matching BiCoord expert.
  action="pinch_grasp" pre-spreads fingers to ~3-4 cm for tiny objects.
  action="release" hovers above target, opens, retreats.

  IMPORTANT — both 'grasp' and 'pinch_grasp' are TOP-DOWN ONLY (fixed quat).
  They WILL FAIL on:
    - Long thin objects whose long axis aligns with the gripper finger spread
      (pen lying along gripper X axis → fingers slide along pen length, no
      width to clamp on, val→0.0).
    - Curved / bent shapes (mug handle, banana clip).
    - Tall narrow objects best grasped from the side.

  After ONE failed top-down attempt (is_holding=False AND
  verify_holding_visual=False), STOP RETRYING TOP-DOWN. Switch to
  get_grasp_pose(object=...) for a learned 6-DoF angle-aware pose, then
  move_to_pose with the returned (xyz, quat). Heuristic top-down can't
  rotate around Z to match the object's geometry — get_grasp_pose can.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "u": 280, "v": 120, "action": "hover", "camera": "head_camera"}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
---

# move_to_pixel · RoboTwin

Single most-useful base skill in zeroshot mode: *"VLM, click on the screen and the gripper goes there."* Wraps capture_image + depth + unprojection + move_to_pose + set_gripper into one call.
