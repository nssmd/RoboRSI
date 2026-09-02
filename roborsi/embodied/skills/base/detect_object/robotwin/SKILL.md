---
name: detect_object
kind: base
robot: robotwin
version: 0.1.0
description: |
  Low-level open-vocabulary object detection + segmentation via Grounding-DINO
  + SAM (Rollout Tier 2 perception). Pass a noun phrase ("red cube",
  "hammer head", "silver bowl"), get back bounding box + mask centroid +
  detection confidence. NO VLM in the loop — fully deterministic.

  THIS SKILL IS THE PERCEPTION CORE: the real Grounding-DINO + SAM
  implementation (model singleton, per-frame cache, `detect()` pure
  function, `Detection` dataclass) lives in this skill's policy.py. Every
  other perception consumer imports `detect` from here, so the whole sim
  perception stack is inside the self-evolving skill closure.

  This is the building block under find_pixel / get_object_bbox. Call it
  directly when you want all top-K detections (e.g. multiple instances of
  the same object class) instead of just the top one.
metadata:
  tags: [base, perception, grounded-sam, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"object": "silver bowl"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
args:
  object:           { type: string, required: true, description: "noun phrase (concrete, no demonstratives)" }
  top_k:            { type: integer, description: "max detections to return, default 5" }
  box_threshold:    { type: float, description: "Grounding-DINO box conf threshold, default 0.25" }
  text_threshold:   { type: float, description: "Grounding-DINO text alignment threshold, default 0.20" }
returns:
  ok: bool
  detections: list   # each: {bbox: [u0,v0,u1,v1], centroid: [u,v], score: float}
  best: dict         # convenience: highest-score detection (same shape)
when_to_use: |
  When you need to enumerate multiple instances ("all the cubes"), or pick
  the highest-confidence among several candidates. For single-best lookup
  use find_pixel — it wraps detect_object and returns just .best.
---

# detect_object · Grounded-SAM

This skill IS the perception core — the real Grounding-DINO + SAM
implementation lives here in `policy.py` (module-level `detect()` pure
function, `Detection` dataclass, model singleton + per-frame cache). It is
NOT a thin wrapper over another module. Other consumers (find_pixel,
get_object_bbox, segment_object_pointcloud, multi_view_fusion,
propose_keypoints, localize_object_top_center, grasp_object) import
`detect` from `detect_object.policy`.

Models are lazy-loaded once per process (Grounding-DINO ~172M, SAM-base
~94M, both on CUDA). `detect()` is pure (numpy in → list[Detection] out)
and imports no sim/rollout code, so it stays free of circular imports.
