---
name: skill_mint
kind: lifecycle
lifecycle: minting
version: 0.1.0
description: Auto-mint a new task skill from a DataStore label that's been accumulating successful rollouts. Writes SKILL.md + bundle.yaml so the task becomes a first-class RoboRSI citizen.
metadata:
  tags: [meta, evolution, skill-minter]
  related_skills: [expert_replay, lerobot_build, pi0_finetune]
params:
  source_label:   { type: string, required: true, description: "DataStore skill/label to promote." }
  new_task_name:  { type: string, required: true }
  domain:         { type: string, default: manipulation }
  description:    { type: string, required: true }
  min_successes:  { type: int,    default: 20, description: "Refuse to mint below this success count." }
  backends:       { type: array,  default: [robotwin] }
  overwrite:      { type: bool,   default: false }
---

# skill_mint — "earn your place in the catalogue"

## Overview

This is the skill that closes the flywheel:

1. You collect rollouts under some ad-hoc label (say `handover_block_wip`).
2. Once enough successes have piled up, `skill_mint` promotes that label
   into a real task skill directory with `SKILL.md` + `bundle.yaml`.
3. After minting, `roborsi task list` / `roborsi plan` see the new
   task and can use it in long-horizon plans.

The minting step is **on purpose** explicit — we don't want every label
leaking into the catalogue. You decide when a label has earned it.

## Phases

1. Scan `DataStore.list(source_label)`. Abort unless
   `successes >= min_successes`.
2. Compute the aggregate meta (backends seen, cameras seen, task names).
3. Generate `SKILL.md` (kind=task) from a template, filling in
   description, backends, frontmatter tags derived from sampled metas.
4. Generate `bundle.yaml` with the default pipeline
   (expert_replay → lerobot_build → pi0_finetune → evaluate_expert).
5. Write under `roborsi/embodied/skills/<domain>/<new_task_name>/`.

## Success criteria

- `SKILL.md` + `bundle.yaml` land on disk.
- `discover()` after minting returns the new task.

## Failure modes

| Symptom | Fix |
|---|---|
| `FileExistsError: … already exists` | Re-run with `overwrite=true`, or delete the existing dir. |
| `RuntimeError: only N successes, need M` | Collect more data before minting. |

## Why

Without this skill, "auto-evolution" is aspirational. With it, the
platform can **point at its own footprint on disk** and say "here are
the N tasks I mastered by myself".
