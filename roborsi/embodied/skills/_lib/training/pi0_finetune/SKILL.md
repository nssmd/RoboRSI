---
name: pi0_finetune
kind: lifecycle
lifecycle: training
version: 0.1.0
description: Finetune π₀ or π₀.₅ on a RoboRSI-built LeRobot dataset. Wraps lerobot-train under the hood.
metadata:
  tags: [training, pi0, finetune, lerobot]
  related_skills: [lerobot_build, success_rate, pi0_posttrain]
params:
  dataset:       { type: string, required: true, description: "LeRobot dataset name or path." }
  base_model:    { type: string, default: pi0.5, description: "pi0 | pi0.5 | pi0-fast" }
  steps:         { type: int,    default: 20000 }
  batch_size:    { type: int,    default: 8 }
  lr:            { type: float,  default: 2.5e-5 }
  output_dir:    { type: string, description: "Override checkpoint dir (default ~/.roborsi/checkpoints/<dataset>-<base>)." }
  device:        { type: string, default: cuda }
  wandb:         { type: bool,   default: false }
---

# pi0_finetune — π₀ / π₀.₅ finetune on RoboRSI datasets

## Overview

Finetune a pretrained π₀ / π₀.₅ checkpoint on a single-task LeRobot
dataset produced by `dataset/lerobot_build`. No multi-task mixing in
this skill — that belongs in a future `training/pi0_multitask` sibling.

## When to use

- You have ≥ 50 successful episodes for a task.
- You want a fast-inference policy for this task that replaces
  `expert_replay` or `rollout_vlm` in `execution` mode.

## Phases

1. Validate dataset: `LeRobotDataset(dataset)` loads; camera set matches
   the policy's expected input (else fail loud).
2. Resolve base checkpoint: `physical-intelligence/pi0.5` or local path.
3. Launch `lerobot-train` subprocess with CLI args derived from `params`.
4. Stream logs; persist `output_dir/training_state.json` + final
   checkpoint.
5. Return summary: final step, val loss (if split provided), output path.

## Success criteria

- Final checkpoint directory non-empty.
- Val loss (if any) monotonic-ish (no divergence).
- Hook downstream `evaluation/success_rate` to verify actual robot
  performance — loss is not a success proxy for manipulation.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| OOM on H100 | Batch size too big for base model | Drop to 4 or enable `gradient_accumulation_steps` |
| Dataset schema mismatch | Camera names differ from pretrain | Rename in `dataset/lerobot_build` params |
| Loss plateaus high | Too few episodes or data too uniform | Collect more / add domain randomization |

## Implementation status

**SKELETON.** The shipped `policy.py` prints the would-be `lerobot-train`
command and exits. Wiring the actual subprocess requires deciding on the
checkpoint cache location and pulling in HF auth / proxy handling from
`roborsi.config.loader`. Lands in the next slice.

## Why a skill and not a script

Because the task bundle needs to say "train then evaluate" declaratively
without shell-script glue. Everything a task does is a skill.
