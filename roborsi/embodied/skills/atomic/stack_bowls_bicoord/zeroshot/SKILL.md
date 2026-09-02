---
name: stack_bowls_bicoord.zeroshot
kind: atomic_subskill
parent: stack_bowls_bicoord
phase: zeroshot
version: 0.1.0
description: VLM zero-shot attempt at stack_bowls_bicoord. zero-shot stack_bowls_bicoord
metadata:
  tags: [zeroshot, vlm, sim, robotwin, auto-authored]
params:
  episodes:    { type: int, default: 1 }
  seed_start:  { type: int, default: 0 }
  tool_budget: { type: int, default: 25 }
---

# stack_bowls_bicoord.zeroshot

Runnable zero-shot entry for `stack_bowls_bicoord` — drives one VLM rollout episode.
