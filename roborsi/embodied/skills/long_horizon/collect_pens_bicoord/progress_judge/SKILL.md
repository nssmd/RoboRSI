---
name: collect_pens_bicoord.progress_judge
kind: long_horizon_subskill
parent: collect_pens_bicoord
phase: progress_judge
description: Per-phase Claude-subprocess VLM judge. Sees the latest head-camera frame and reports how many pens are inside the cup vs still on the table.
metadata:
  uses_lib: [_lib.judging.vlm_judge_claude]
params:
  scene_image: { type: string, required: true }
  expected_remaining: { type: int, default: 0 }
returns:
  done:  "bool"
  score: "float (pens_in_cup / 4)"
  reason: "str"
---

# collect_pens_bicoord / progress_judge

Asks Claude to count `pens_in_cup` and `pens_on_table` from a single image; returns done=True only when all 4 pens are in the cup. Score is fractional progress.
