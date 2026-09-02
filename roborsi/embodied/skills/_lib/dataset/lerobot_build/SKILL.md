---
name: lerobot_build
kind: lifecycle
lifecycle: dataset
version: 0.1.0
description: Aggregate DataStore episodes for a skill/task into a LeRobot v0.5 dataset (meta/info.json, data/*.parquet, videos/) ready for π₀ training.
metadata:
  tags: [dataset, lerobot, pi0]
  related_skills: [expert_replay, pi0_finetune]
params:
  skill_label:     { type: string, required: true, description: "DataStore skill folder to aggregate." }
  dataset_name:    { type: string, required: true }
  splits:          { type: object, default: {train: 1.0} }
  fps:             { type: int,    default: 30 }
  robot_type:      { type: string, default: aloha-agilex }
  out_root:        { type: string, description: "Override datasets root (default: ~/.roborsi/datasets)." }
---

# lerobot_build — DataStore episodes → LeRobot v0.5 dataset

## Overview

RoboRSI's `DataStore` writes episodes in a rollout-centric layout
(`<skill>/<run_id>/episode.parquet + frames/ + meta.json`). π₀ / π₀.₅
training consumes the LeRobot v0.5 dataset layout (`meta/info.json`,
`meta/episodes.jsonl`, `data/chunk-xxx/episode_<i>.parquet`,
`videos/`). This skill converts the former into the latter.

## When to use

- After you've collected enough episodes via `expert_replay` (typically
  ≥ 50 successful runs for a tabletop task).
- Before invoking `training/pi0_finetune`.
- When preparing a release snapshot.

## Phases

1. Scan `DataStore.list(skill_label)`; drop failed episodes unless
   `--include-failed` is set.
2. Build `meta/info.json` with features, fps, robot_type, codebase_version.
3. Write `meta/episodes.jsonl` (one line per episode with length).
4. Write each episode's per-frame rows into `data/chunk-000/episode_<i>.parquet`.
5. Encode image sequences into `videos/observation.images.<cam>/episode_<i>.mp4`
   via `lerobot`'s video encoder.
6. Emit final summary: episodes, frames, disk usage.

## Output

```
<out_root>/<dataset_name>/
  meta/
    info.json
    episodes.jsonl
    tasks.jsonl
  data/chunk-000/episode_000.parquet
  videos/observation.images.<cam>/episode_000.mp4
```

## Success criteria

- `lerobot.datasets.lerobot_dataset.LeRobotDataset(dataset_name)`
  instantiates without errors.
- `len(ds)` equals total frames across all included episodes.

## Why LeRobot and not our own format

- π₀ / π₀.₅ / RDT / DP / ACT all eat LeRobot directly — zero
  retraining-infra work.
- LeRobot's dataset format is the closest thing to a community standard
  right now; betting elsewhere duplicates conversion burden.

## Implementation note

Uses `lerobot` (already vendored in `roborsi/embodied/engine/`). The
`LeRobotDataset.create(...)` helper handles most of the schema; we
supply frame iterators from parquet.
