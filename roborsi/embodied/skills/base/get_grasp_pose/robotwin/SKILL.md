---
name: get_grasp_pose
kind: base
robot: robotwin
category: policy
version: 0.1.0
description: |
  PREFERRED grasp planner — call this FIRST for any pick. Runs a learned 6-DoF
  grasp generator (GraspGen / GraspNet-baseline) on the depth+RGB at the named
  object and returns top-K ranked grasp poses with correct gripper orientation
  built in. The returned `grasp_pose` already has the 0.18 m fingertip offset
  baked in, so it's directly usable with `move_to_pose`.

  Use this INSTEAD OF improvising hover→descend→close with `move_to_pose`
  alone for any pick. Heuristic top-down picks miss frequently on:
    • small objects (≤5 cm) where pixel→world reprojection is noisy
    • objects inside containers (block in bowl, item in box) — `z_min`/`z_max`
      let the network ignore the container walls
    • elongated / curved shapes (pen, handle) where finger axis must rotate
    • cluttered scenes
when_to_use: |
  CALL THIS FIRST for any pick — before calling move_to_pose at all. Don't
  improvise hover→descend on a pixel; let the learned model pick the
  orientation and approach for you.

  WORKFLOW (pick a small block sitting in a bowl):
    look(camera='head_camera')
    p  = find_pixel(object='red cube inside left bowl')
    xyz = unproject_pixel(u=p['u'], v=p['v'])['xyz']    # to set z filter
    r  = get_grasp_pose(object='red cube inside left bowl',
                        z_min=xyz[2]-0.005, z_max=xyz[2]+0.04,
                        half_window_px=30)              # narrow window for tiny obj
    pose = r['grasp_pose']                              # [x,y,z, qx,qy,qz,qw]
    gripper(arm='left', action='open')
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2]+0.10, quat=pose[3:])  # hover
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2],      quat=pose[3:])  # descend
    gripper(arm='left', action='close')
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2]+0.20, quat=pose[3:])  # lift
    verify_holding_visual(arm='left', object='red cube')

  CRITICAL knobs:
    • `half_window_px=30` for objects ≤3 cm; default 60 for hand-sized.
    • `z_min`/`z_max` MUST be set when grasping inside a container; otherwise
      the point cloud includes bowl/box walls and the model returns a grasp
      that picks up the container, not the contents.
    • If returned `backend == 'heuristic_topdown'`, the learned model didn't
      load — that pose is a fallback, no better than improvising. Stop and
      report.
args:
  object: { type: string, description: "object name (will run find_pixel internally)" }
  u: { type: int, description: "alternative: pixel x (use with v)" }
  v: { type: int, description: "alternative: pixel y" }
  camera: { type: string, default: head_camera }
  half_window_px: { type: int, default: 60, description: "workspace mask radius around (u,v). Use 30 for tiny/thin objects, 80 for larger." }
  top_k: { type: int, default: 5, description: "how many ranked grasps to return; pick the highest-score one." }
  z_min: { type: float, description: "world-frame Z lower bound (m). Filter point cloud — CRUCIAL for cube-in-bowl: pass z_min=cube_z-0.01 so the network ignores bowl walls." }
  z_max: { type: float, description: "world-frame Z upper bound (m). Pass cube_z+0.05 to limit to object volume." }
returns:
  ok: bool
  backend: str  # 'graspnet_baseline' (real inference) or 'heuristic_topdown' (fallback)
  grasp_pose: [x, y, z, qx, qy, qz, qw]
  score: float
  candidates: list   # top_k poses, ranked
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl on the right", "arm": "right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---

# get_grasp_pose · RoboTwin

Calls GraspNet-baseline (open-source, GraspGen family) on depth + RGB at
the named object. Returns top-K 6-DoF gripper poses ranked by a learned
grasp-quality score.

## Why this exists

`move_to_pixel(grasp)` is heuristic top-down: descends straight down with
fixed gripper orientation. Fails for many common objects (long thin pens,
curved handles, narrow tall items). A learned grasp generator picks
orientations that actually grip the object — no heuristics required.

## Failure mode signal

If `backend == 'heuristic_topdown'`, the GraspNet inference path fell
back. Either the checkpoint is missing or inference errored. The returned
pose is no better than `move_to_pixel(grasp)`. Don't keep calling
get_grasp_pose if it's stuck on heuristic — switch tactic.
