---
name: clean_table.progress_judge
kind: long_horizon_subskill
parent: clean_table
phase: progress_judge
description: After each atomic in the plan, judge whether it actually moved the world toward done. Wraps _lib.judging.progress_score.
---

# clean_table / progress_judge

Thin wrapper. Each call:
- input: phase label + image_path + expected text
- output: `{done, score, reason}`
- runtime decides continue / retry / replan
