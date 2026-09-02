---
name: stack_bowls_bicoord.reset_success
kind: atomic_subskill
parent: stack_bowls_bicoord
phase: reset_success
version: 0.1.0
description: After a successful stack_bowls_bicoord episode, reset to a fresh BiCoord scene; log a successful reset trajectory.
metadata:
  tags: [reset, sim, bicoord]
params:
  env:        { type: object, required: true }
  next_seed:  { type: int }
  log:        { type: bool, default: true }
returns:
  ok: "bool"
---

# stack_bowls_bicoord / reset_success

Trivial in sim — `env.reset(next_seed)` regenerates the BiCoord stack_bowls scene. Logged as `stack_bowls_bicoord_reset_success` for later distillation.
