---
name: stack_bowls_demo
kind: long_horizon
domain: manipulation
version: 0.1.0
description: Minimal long_horizon over BiCoord stack_bowls — drives plan→atomic(stack_bowls_bicoord)→progress_judge for one phase. Designed to demonstrate the framework using a trained atomic policy (rather than VLM zeroshot).
metadata:
  tags: [long_horizon, bicoord, stacking, trained-atomic-demo]
  sim_task: stack_bowls
  sim_backend: bicoord
  candidate_atomics: [stack_bowls_bicoord]
  vlm_prompts:
    instruction: "Stack one bowl onto the other so they end up nested."
    decompose_hint: "There is a single physical phase here — emit exactly one step calling stack_bowls_bicoord."
    progress_check: "Top bowl is centered on top of bottom bowl, both upright."
  posttrain:
    reward_recipe: "binary phase reward (1 if check_success else 0)"
---

# stack_bowls_demo (long_horizon)

The point of this skill is **not** task complexity — it's to prove the full
plumbing:

```
plan → 1 step (stack_bowls_bicoord)
spawn ONE bicoord env (sim_task=stack_bowls, seed=N)
phase 0: stack_bowls_bicoord — looks up active_executor.json
    → "policy:<ckpt>" → load policy + rollout on live env (NO VLM)
    → "zeroshot"      → VLM tool loop
progress_judge → did the bowls end up stacked?
JSON report
```

If `~/.roborsi/atomic_state/stack_bowls_bicoord/active_executor.json`
points at a checkpoint, the trained policy drives the arm at 30 Hz —
no VLM in the inner loop.
