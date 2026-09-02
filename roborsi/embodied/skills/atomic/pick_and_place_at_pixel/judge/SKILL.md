---
name: pick_and_place_at_pixel.judge
kind: atomic_subskill
parent: pick_and_place_at_pixel
phase: judge
version: 0.1.0
description: VLM-as-judge for pick_and_place_at_pixel. Spawns a separate Claude process to look at pre/post images and decide whether the named source object ended up on the named target zone. Replaces sim-side check_success.
metadata:
  tags: [judging, vlm, claude]
  uses_lib: [_lib.judging.vlm_judge_claude]
params:
  source_object: { type: string, required: true }
  target_zone:   { type: string, required: true }
  pre_image:     { type: string, description: "absolute path to pre-action frame" }
  post_image:    { type: string, required: true, description: "absolute path to post-action frame" }
  context:       { type: string, default: "" }
returns:
  success: "bool"
  reason:  "str"
---

# pick_and_place_at_pixel / judge

Per-atomic VLM judge. Caller passes the source/target names + image paths from the rollout. We hand them to `_lib.judging.vlm_judge_claude` with a criterion derived from the parent SKILL.md `expected_on_success`.
