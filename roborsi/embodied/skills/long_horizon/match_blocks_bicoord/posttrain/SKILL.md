---
name: match_blocks_bicoord.posttrain
kind: long_horizon_subskill
parent: match_blocks_bicoord
phase: posttrain
description: Skeleton — once we have N successful long-horizon traces, replay them with sparse phase rewards into _lib.rl.pi0_posttrain on pick_and_place_at_pixel.
---

# match_blocks_bicoord / posttrain

Skeleton. Future: per-phase judge results become sparse rewards for RL fine-tuning of the underlying atomic policy.
