---
name: clean_table.posttrain
kind: long_horizon_subskill
parent: clean_table
phase: posttrain
description: After completing (or failing) a long-horizon execution, use the trace to jointly RL-finetune the policies of participating atomics.
---

# clean_table / posttrain

**Skeleton.** Will eventually:
1. Read the latest execution trace (each atomic's input obs / chosen action / outcome).
2. Compute a sparse reward per phase boundary using `progress_judge` outputs.
3. Trigger `_lib.rl.pi0_posttrain` on each participating atomic's checkpoint.

For now this skill raises NotImplementedError so the contract is visible without
faking work.
