---
name: collect_pens_bicoord
kind: long_horizon
domain: manipulation
version: 0.1.0
description: BiCoord-Bench collect_pens — 4 markpens scattered on a tabletop must each be picked and dropped into a static cup. Decomposes into N invocations of pick_and_place_at_pixel (one per remaining pen), VLM zeroshot per atomic, Claude-subprocess VLM judge per atomic, and a progress judge that counts pens-in-cup at each phase.
metadata:
  tags: [bimanual, pick-and-place, sim, bicoord, long_horizon]
  embodiments: [aloha-agilex]
  backends: [bicoord]
  sim_task: collect_pens
  candidate_atomics: [pick_and_place_at_pixel]
  vlm_prompts:
    user_instruction: "Pick up every marker pen from the table and drop it into the cup."
    scene_hint: "Tabletop with 4 marker pens scattered in front of a static pencup. Pens may lie flat in any orientation. The cup is upright."
    plan_hint: "For each pen still on the table, dispatch one pick_and_place_at_pixel(source_object='pen_<i>', target_zone='cup', arm=<left if pen on left half / right otherwise>). Aim for 4 atomic steps."
  progress_criterion: "All 4 marker pens are inside the cup; none remain on the tabletop."
---

# collect_pens_bicoord (long_horizon)

End-to-end VLM-driven pipeline (no expert_replay anywhere), run through
the 3-role triangle (`LHPlanner → LHExecutor → LHReviewer`):

1. LHPlanner — decomposes into ordered pick-and-place atomics (one per pen), pairing each pen with the closer arm.
2. LHExecutor — for each atomic, runs the active executor (zeroshot rollout VLM tool loop, or trained policy once eval flips) on the shared env with a per-atomic Reviewer judging success.
3. LHReviewer — issues the overall verdict and optionally proposes a base-skill fix.
4. `posttrain/` — skeleton; once we have N successful long-horizon traces, replay into RL fine-tuning.

Crucially: every success bit (per-atomic and overall) is decided by a fresh
Claude subprocess looking at images — no sim-privileged state.
