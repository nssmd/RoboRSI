---
name: success_rate
kind: lifecycle
lifecycle: evaluation
version: 0.1.0
description: Run a sim task N seeds with a chosen executor (expert / policy checkpoint / VLM), report success rate and per-seed outcome.
metadata:
  tags: [evaluation, sim]
  related_skills: [expert_replay, pi0_finetune, progress_score]
params:
  task:         { type: string, required: true }
  executor:     { type: string, default: expert, description: "expert | pi0_checkpoint | rollout_vlm" }
  backend:      { type: string, default: robotwin }
  seeds:        { type: int,    default: 20 }
  seed_start:   { type: int,    default: 1000, description: "Offset from training seeds to avoid leakage." }
  checkpoint:   { type: string, description: "Path for pi0_checkpoint executor." }
---

# success_rate — task-level evaluation

## Overview

Deterministic evaluation for a single task: pick an executor, run it on a
held-out seed range, report:

```
{
  "task": "beat_block_hammer",
  "executor": "expert",
  "seeds": 20,
  "successes": 18,
  "success_rate": 0.90,
  "per_seed": [{"seed": 1000, "success": true, "outcome": "success"}, ...]
}
```

## Why held-out seeds

Training data uses seeds `0..N`. Evaluation must use seeds `seed_start..`
(default 1000) to avoid trivial memorisation. The `seed_start` offset
is the only reliable way to prevent test/train leakage in
sim-deterministic tasks.

## Executors

- `expert`: drives backend's `run_expert` (the baseline).
- `pi0_checkpoint`: loads a LeRobot policy from `checkpoint`, steps the
  env in-loop. Requires `SimEnv.step(action)` to be implemented for the
  backend (MVP: not yet).
- `rollout_vlm`: VLM + tools zero-shot path (requires Toolkit).

## Phases

1. Resolve executor; if `pi0_checkpoint` or `rollout_vlm`, assert
   dependencies present.
2. For each seed in `range(seed_start, seed_start + seeds)`:
   1. `env.reset(seed)` → execute to completion → `check_success`.
3. Compute stats, persist `eval_report.json` next to checkpoint (for
   `pi0_checkpoint`) or under `~/.roborsi/evals/<task>/<timestamp>/`.

## Success criteria

- Report written, success_rate computed over exactly `seeds` trials.
- No seed reuse across runs unless caller explicitly does so.

## Implementation status

Only the `expert` executor is wired. `pi0_checkpoint` and `rollout_vlm`
executors raise `NotImplementedError` until their dependencies land.
