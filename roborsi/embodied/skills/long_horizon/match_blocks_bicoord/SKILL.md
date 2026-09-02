---
name: match_blocks_bicoord
kind: long_horizon
domain: manipulation
version: 0.1.0
description: BiCoord-Bench match_blocks_with_signs — 3 colored cuboid blocks (~5×5×12cm) on the table left side, 3 colored signs on the right side. Move each block to the spot in front of the matching-color sign. Pick-and-place atomic, VLM judge per phase, progress judge counts blocks-on-correct-spot.
metadata:
  tags: [bimanual, pick-and-place, sim, bicoord, long_horizon]
  embodiments: [aloha-agilex]
  backends: [bicoord]
  sim_task: match_blocks_with_signs
  candidate_atomics: [pick_and_place_at_pixel]
  vlm_prompts:
    user_instruction: "Move each colored block to the spot in front of its matching colored sign."
    scene_hint: "Tabletop with 3 colored cuboid blocks (~5cm × 5cm × 12cm) on the LEFT half of the table, and 3 colored signs on the RIGHT half. Each block must be moved next to the sign of the same color."
    plan_hint: "For each block, emit one pick_and_place_at_pixel step where source_object='<color> block' and target_zone='spot in front of <color> sign'. Use left arm (blocks are on the left). Aim for 3 atomic steps."
  progress_criterion: "All 3 blocks rest on the table near their matching-color signs."
---

# match_blocks_bicoord (long_horizon)

Friendly grasp benchmark — 5cm cuboid blocks (vs the 1cm pens of
collect_pens). Top-down grasp + finger spread + sim physics all in the
comfortable regime: fingers clamp on a flat side, no rolling, no
finger-vs-thin-cylinder geometry headaches.

Long-horizon execution runs through the 3-role triangle
(`LHPlanner → LHExecutor → LHReviewer`): the LHPlanner decomposes into
ordered atomics (block + matching sign pairs), the LHExecutor drives each
atomic on the shared env with a per-atomic Reviewer, and the LHReviewer
issues the overall verdict.

This task is the demo proof — pens fail because of geometry, blocks
exercise the same pipeline on shapes the dual-finger gripper actually
handles.
