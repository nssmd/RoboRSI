---
name: segment_object_pointcloud
kind: base
domain: perception
version: 0.1.0
description: |
  Clean multi-view point cloud of a named object. Runs Grounded-SAM on each
  camera, optionally verifies each mask with a sub-VLM yes/no check ("is
  this really {object}?"), unprojects survivors to world XYZ, fuses across
  cameras. Replaces ad-hoc per-skill SAM+unproject pipelines and shields
  downstream PCA / functional-point estimation from GroundingDINO
  false-positives (e.g. red robot-base detected as "coloured block").
args:
  object:
    type: string
    required: true
    description: noun phrase for the object to segment (e.g. 'hammer', 'coloured block')
  ee_xyz:
    type: list
    description: optional EE world XYZ; drops points farther than ee_radius_m
  ee_radius_m:
    type: float
    default: 0.20
    description: radius in meters for the ee_xyz distance filter
  vlm_verify:
    type: bool
    default: true
    description: per-camera sub-VLM yes/no check on the masked crop
  min_pixels:
    type: int
    default: 30
    description: drop a camera if the surviving mask has fewer pixels than this
  cameras:
    type: list
    description: subset of camera names to use (defaults to all available)
  max_points:
    type: int
    default: 5000
    description: downsample the fused cloud to at most this many points
  cluster_strategy:
    type: string
    default: vlm
    description: how to pick the object cluster among segmented points
metadata:
  tags: [perception, sam, vlm, pointcloud]
  embodiments: [aloha-agilex, franka-panda]
  backends: [robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl", "ee_xyz": null, "vlm_verify": false}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---

# segment_object_pointcloud (base)

## Inputs
- `object` (str, required): noun phrase, e.g. "hammer", "coloured block"
- `ee_xyz` (list[float], optional): if given, drop points farther than `ee_radius_m`
- `ee_radius_m` (float, default 0.20)
- `cameras` (list[str], optional): subset; defaults to all available
- `vlm_verify` (bool, default true): per-camera sub-VLM yes/no on the masked crop
- `min_pixels` (int, default 30): drop a camera if surviving mask < this many px

## Outputs
- `xyz` (list[list[float]]): fused world points (downsampled to <= 5000)
- `centroid` (list[float])
- `per_camera`: per-cam {n_mask_pix, n_after_ee_filter, vlm_verdict, contribution_pts}
- `cameras_used` (list[str])
