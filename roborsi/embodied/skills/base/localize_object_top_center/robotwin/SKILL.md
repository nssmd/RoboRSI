---
name: localize_object_top_center
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: |
  Rollout coarse→fine localization in a single call: returns the world XYZ
  of the geometric center of a named object's TOP face, with <1cm accuracy.

  Pipeline (no oracle, fully observation-based):
    1. Grounded-SAM detects the named object in head_camera.
    2. Overlay grid_n^2 numbered candidate points INSIDE the mask.
    3. Sub-VLM call: "which numbered dot is the center of the object's
       TOP face?" → returns an index.
    4. Depth-unproject that index's pixel to world XYZ.
    5. (Optional) Refine z by averaging the top-z-band of the mask cloud
       so the returned z is the TOP surface, not a sloped side.

  Use this whenever you need to place something on / tap something on a
  small object and the success tolerance is ≤2cm (e.g. beat_block_hammer,
  press button, stack blocks, place_cans_plasticbox center alignment).
metadata:
  tags: [perception, fine, rollout, sim]
  base_tools_used: [find_pixel, label_points_grid, unproject_pixel]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl on the right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
args:
  object:        { type: string, required: true, description: "Noun phrase Grounded-SAM can find, e.g. 'red cube', 'coloured block', 'mug rim'." }
  grid_n:        { type: int, default: 5, description: "Square grid size for candidate points inside the mask. 5 → 25 candidates." }
  top_band_m:    { type: float, default: 0.005, description: "Take the top-Z 5mm slice of the mask cloud for the returned z (gives true top face, not the chosen pixel's z which can be on a sloped side)." }
returns:
  ok: bool
  xyz: list[float]   # world XYZ of the chosen point on the object's top
  chosen_label: int  # which numbered grid candidate the sub-VLM picked
  candidates: dict   # {label: [u,v]} all options the VLM saw
  labeled_image_path: str
  note: str
when_to_use: |
  Replace `find_pixel + unproject_pixel` whenever you need ≤2cm placement
  precision on a small, mostly-convex object. Costs +1 sub-VLM call but
  cuts perception XY error from ~3-5cm (coarse mask centroid) to ~0.5-1cm.
---

# localize_object_top_center · robotwin (Rollout coarse→fine fused)

Bakes Rollout's "set-of-mark" precise-pointing pattern (Sec. III-A) into a
single tool call. Composes existing base skills (`label_points_grid` with
`mask_from_query`, `_unproject_pixel`) and adds a single internal VLM call
to choose the best on-mask candidate.

Implementation in `policy.py::dispatch_runtime`.
