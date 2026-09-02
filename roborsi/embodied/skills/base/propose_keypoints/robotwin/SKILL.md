---
name: propose_keypoints
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: |
  ReKep-style semantic keypoint proposer. For a named object: SAM-mask it,
  extract DINOv2 patch features inside the mask, k-means(cosine) cluster
  into K groups. Returns K cluster-centroid pixels. Each centroid lands on
  an anatomically meaningful sub-region (head vs handle, strike face vs
  claw, bottle neck vs body). MUCH better than uniform-stride sampling
  when the object has color/texture variation that maps to semantic parts.
  Pair with label_points_grid or unproject_pixel to get world XYZ for
  fine-grained Set-of-Mark VLM picking.

  THIS SKILL IS THE REAL ReKep IMPLEMENTATION: the DINOv2 feature
  extraction, k-means(cosine) clustering and `propose_semantic_keypoints()`
  live in this skill's policy.py (not a wrapper). SAM grounding reuses
  `detect` from the detect_object perception core.
args:
  object: { type: string, required: true, description: "Noun phrase for SAM grounding." }
  k: { type: int, required: false, description: "Number of cluster centroids to return (default 5)." }
  camera: { type: string, required: false, description: "Camera (default head_camera)." }
  min_pixel_separation: { type: int, required: false, description: "Drop centroids closer than this many pixels to a stronger one (default 8)." }
returns:
  ok: bool
  keypoints_uv: list[list[int]]   # K centroid pixels [(u, v), ...]
  camera: str
  n_clusters_returned: int
when_to_use: |
  When picking a SPECIFIC sub-part of an object (the metal head of a
  hammer vs the wooden handle, the lid of a bottle vs the body). Use
  output as candidates for VLM Set-of-Mark picking — VLM picks one of K
  IDs and you unproject that pixel to world XYZ for grasp/tap.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
---

# propose_keypoints · ReKep semantic keypoints

This skill IS the real ReKep keypoint-proposal implementation — DINOv2
patch-feature extraction + k-means(cosine) clustering live in `policy.py`
(module-level `propose_semantic_keypoints()`, DINOv2 model singleton). It
is NOT a thin wrapper. SAM grounding reuses `detect` from the
detect_object perception core (imported at module level — safe, since
detect_object.policy pulls in no sim/rollout code). rollout_runtime is
imported lazily inside `dispatch_runtime` only, avoiding circular imports.
