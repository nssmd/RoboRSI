---
name: stack_bowls_bicoord.reset_failure
kind: atomic_subskill
parent: stack_bowls_bicoord
phase: reset_failure
version: 0.1.0
description: After a failed stack_bowls_bicoord episode, classify failure mode and reset; log recovery trace under stack_bowls_bicoord_reset_failure_<mode>.
metadata:
  tags: [reset, sim, bicoord]
params:
  env:                { type: object, required: true }
  next_seed:          { type: int }
  failure_mode_hint:  { type: string }
returns:
  ok: "bool"
  failure_mode: "string"
---

# stack_bowls_bicoord / reset_failure

Mirror of click_bell.reset_failure. Sim-trivial: env.reset(next_seed) or homestate fallback. Failure mode label distinguishes failure traces for later replay.
