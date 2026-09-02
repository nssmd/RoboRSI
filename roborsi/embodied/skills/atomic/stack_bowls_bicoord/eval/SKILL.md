---
name: stack_bowls_bicoord.eval
kind: atomic_subskill
parent: stack_bowls_bicoord
phase: eval
version: 0.1.0
description: Evaluate latest stack_bowls_bicoord checkpoint on held-out seeds (BiCoord stack_bowls); flip atomic.active_executor to policy:<ckpt> when success_rate >= threshold.
metadata:
  tags: [evaluation, sim, bicoord, act]
  uses_lib: [_lib.evaluation.success_rate]
params:
  seeds:        { type: int,    default: 5 }
  seed_start:   { type: int,    default: 1000 }
  threshold:    { type: float,  default: 0.05 }
  checkpoint:   { type: string, description: "Override; defaults to latest under ~/.roborsi/checkpoints/.../stack_bowls_bicoord*/" }
  executor:     { type: string, default: pi0_checkpoint }
returns:
  success_rate: "float"
  switched:     "bool"
---

# stack_bowls_bicoord / eval

Same shape as click_bell.eval. Threshold is purposefully low — long_horizon plumbing is what we're validating, not policy quality.
