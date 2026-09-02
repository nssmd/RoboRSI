---
name: collect_pens_bicoord.posttrain
kind: long_horizon_subskill
parent: collect_pens_bicoord
phase: posttrain
description: Skeleton — once we have N successful long-horizon traces, replay them with sparse phase rewards into _lib.rl.pi0_posttrain on pick_and_place_at_pixel.
---

# collect_pens_bicoord / posttrain

Skeleton. Future: per-phase judge results become sparse rewards for RL fine-tuning of the underlying atomic policy.
