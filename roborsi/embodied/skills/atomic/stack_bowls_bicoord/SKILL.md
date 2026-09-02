---
name: stack_bowls_bicoord
kind: atomic
domain: manipulation
version: 0.1.0
description: BiCoord-Bench's stack_bowls — bimanual; one arm picks a bowl and stacks it on the other bowl. Demo task for the BiCoord backend in RoboRSI; expert play_once works at ~80% on seeds 1-5.
metadata:
  tags: [bimanual, stacking, sim, bicoord]
  embodiments: [aloha-agilex]
  backends: [bicoord]
  sim_task: stack_bowls
  vlm_prompts:
    describe_scene: "Two bowls on a tabletop. The robot must stack one bowl onto the other."
    instruction: "Pick up one of the bowls and stack it cleanly onto the other bowl."
    expected_on_success: "Top bowl is centered on top of bottom bowl, both upright, not knocked over."
  active_executor:
    default: zeroshot
    threshold: 0.50
---

# stack_bowls_bicoord (atomic)

Demo task for end-to-end BiCoord integration. Expert `play_once` reliably
solves this at seeds 1, 3, 4, 5 (4/5 = 80% smoke).

## Lifecycle

| sub-skill | what it does |
|---|---|
| zeroshot | VLM + base tools attempt the stack (hard for zeroshot; bootstrap data via expert_replay first) |
| train | LeRobot v3 dataset + ACT/π₀ finetune from collected episodes |
| eval | Held-out seeds; flips active_executor to policy:vN above threshold |
| reset_success | env.reset(next_seed); log to stack_bowls_bicoord_reset_success label |
| reset_failure | env.reset fallback + classify mode; log per-mode datasets for future reset policy |
