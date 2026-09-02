---
name: clean_table_bicoord.posttrain
kind: long_horizon_subskill
parent: clean_table_bicoord
phase: posttrain
description: Skeleton — replay execution traces with sparse phase rewards into per-atomic RL.
---

# clean_table_bicoord / posttrain

Skeleton. Real impl: read trace from atomic_state, compute sparse reward per phase
(via parent's `posttrain.reward_recipe`), trigger `_lib.rl.pi0_posttrain` per atomic.
