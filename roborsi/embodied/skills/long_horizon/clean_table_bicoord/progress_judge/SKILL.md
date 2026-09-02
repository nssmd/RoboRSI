---
name: clean_table_bicoord.progress_judge
kind: long_horizon_subskill
parent: clean_table_bicoord
phase: progress_judge
description: VLM phase-gate for clean_table_bicoord; injects task-specific success criterion.
---

# clean_table_bicoord / progress_judge

Reads parent SKILL.md `vlm_prompts.progress_check` and forwards to `_lib.progress_score`.
