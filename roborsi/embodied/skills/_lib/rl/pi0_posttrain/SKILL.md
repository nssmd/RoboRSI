---
name: pi0_posttrain
kind: lifecycle
lifecycle: rl
version: 0.1.0
description: RL / RLHF-style post-training on top of a π₀ finetune. Uses sim rollouts + a reward source (Progress Judge or task-specific success predicate) as the signal.
metadata:
  tags: [rl, pi0, posttrain]
  related_skills: [pi0_finetune, success_rate, progress_score]
params:
  checkpoint:   { type: string, required: true, description: "π₀ finetune checkpoint to bootstrap from." }
  task:         { type: string, required: true }
  backend:      { type: string, default: robotwin }
  rollouts:     { type: int,    default: 1000 }
  reward:       { type: string, default: success_predicate, description: "success_predicate | progress_score | dense_shape" }
  updates:      { type: int,    default: 200 }
  output_dir:   { type: string }
---

# pi0_posttrain — RL post-training on π₀

## Overview

Take a supervised π₀ finetune and improve it via on-policy RL in sim.
Two reward sources supported:

- `success_predicate`: binary `env.check_success()`.
- `progress_score`: dense signal from the `judging/progress_score` skill
  (VLM-scored phase completion).
- `dense_shape`: distance-to-goal + time penalty (task-specific, requires
  hooks in SKILL.md of the target task).

## When to use

- Finetune success_rate plateaus but is still under expert performance.
- You want a policy more robust to object / pose variations than the
  supervised dataset covered.

## When NOT to use

- Before you've measured finetune success_rate. RL post-train is slow;
  don't spend GPU-weeks squeezing 5 % out of a 30 % finetune — collect
  more data first.

## Phases

1. Load finetune checkpoint.
2. Spawn N parallel envs; roll out policy; compute reward per episode.
3. PPO-style / GRPO-style update (TBD, keeping algorithm choice open).
4. Every K updates, run `evaluation/success_rate`; track drift.
5. Save final checkpoint.

## Implementation status

**SKELETON ONLY.** Algorithm, sim parallelism, rollout buffer, and
update loop are all TODO. Placeholder raises `NotImplementedError` with
a link to this SKILL.md so bundle authors can wire the step and track
when it lights up.

## Why

RL post-train is where the flywheel closes: more sim time → better
policy → fewer human demos needed. Listing it as a first-class skill
now (even as a skeleton) keeps the architecture honest: nothing about
post-training is "somewhere else in the repo" — it's a skill like
everything else.
