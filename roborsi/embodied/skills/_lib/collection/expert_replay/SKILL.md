---
name: expert_replay
kind: lifecycle
lifecycle: collection
version: 0.1.0
description: Drive a sim backend's scripted expert for N seeds; persist each rollout as a RoboRSI DataStore episode (parquet + frames + meta.json).
metadata:
  tags: [data, sim, expert]
  related_skills: [rollout_vlm, lerobot_build]
params:
  task:         { type: string, required: true, description: "Sim task name (e.g. beat_block_hammer)." }
  backend:      { type: string, default: robotwin }
  episodes:     { type: int,    default: 1 }
  seed_start:   { type: int,    default: 0 }
  config:       { type: object, default: {} }
  skill_label:  { type: string, description: "DataStore label; defaults to <task>." }
---

# expert_replay — scripted-expert data collection

## Overview

Every sim backend supported by RoboRSI ships scripted experts (RoboTwin's
`play_once`, ManiSkill's `SolutionEnv`, …). This skill is the thin wrapper
that iterates seeds, calls `env.run_expert`, and drops each resulting
`SimRollout` into the `DataStore`.

It's the **dumbest reliable way** to get clean trajectories. Use it to
bootstrap a dataset before you have any learned policy.

## When to use

- You want a "known-good" seed set for a task.
- You're about to train π₀ / π₀.₅ on this task.
- Parallel farm: N workers run this skill concurrently with disjoint
  `seed_start` ranges.

## When NOT to use

- The expert doesn't exist for your task (→ use `rollout_vlm`).
- The expert is flaky on your embodiment (→ fix the expert, don't wrap
  retries here).

## Phases

1. Resolve backend (`get_backend(backend)`), assert available.
2. For each `seed_start + i` in `range(episodes)`:
   1. `env.run_expert(seed)`.
   2. `DataStore().write(rollout, skill=skill_label)`.
3. Return a JSON summary.

## Success criteria

- Every episode lands on disk with `meta.json.success` reflecting the
  expert's own `check_success`.
- `episodes.hdf5` is NOT produced (we use parquet, not HDF5 — swap later
  if needed).

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `SimBackendUnavailable` | RoboTwin not installed / wrong env | See `docs/sim-robotwin.md` |
| Zero success across 10 seeds | Expert broken on this embodiment | Fix upstream, don't paper over here |
| Disk fills up fast | Default captures start+end obs only; full-fidelity HDF5 would be bigger | Leave as-is for MVP |

## Why

Expert replay is **the honest baseline**. Every evaluation / training run
should measure ΔVM_expert (did the learned policy improve over the expert
or just memorise it?). Treating expert replay as a first-class skill
makes the comparison point explicit.
