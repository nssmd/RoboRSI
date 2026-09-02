---
name: clean_table_bicoord
kind: long_horizon
domain: manipulation
version: 0.1.0
description: BiCoord-Bench's clean_table — bimanual, 4 objects to be moved off the working zone to left/right side targets. First long-horizon task in RoboRSI; runs on bicoord backend.
metadata:
  tags: [long_horizon, bimanual, sim, bicoord, robotwin]
  sim_task: clean_table
  sim_backend: bicoord
  candidate_atomics: [pick_and_place_at_pixel]
  vlm_prompts:
    instruction: "Clear the table — move every object off the central working zone to the left or right side targets, one per arm in parallel where possible."
    decompose_hint: |
      The scene has 4 objects on a table and two arms. For each object emit
      one `pick_and_place_at_pixel` step. The runtime executes each step
      VERBATIM — only these field names work:
        - `object`       (string, REQUIRED; concrete object name, e.g. "red block")
        - `target_side`  (string, REQUIRED; "left" or "right")
        - `arm`          (string; pick the arm closer to the object)
      Do NOT use `source_description` / `target_description` — those fail.
      Emit:
        {"atomic": "pick_and_place_at_pixel",
         "args": {"object": "<concrete>", "arm": "left|right",
                  "target_side": "left|right"},
         "why": "..."}
      Run arms in parallel where ergonomically possible.
    progress_check: |
      After one phase: is the named object now off the central zone and on the
      target side specified? Done iff the object is visibly on the side target.
  posttrain:
    reward_recipe: "binary phase reward (1 if object visibly moved to target side else 0) + small step penalty"
---

# clean_table_bicoord (long_horizon)

## Scene

BiCoord-Bench `clean_table` task. Tabletop has 4 small object actors. Two
arms (aloha-agilex). Two side targets (left and right zones). Goal: move
every object to its assigned side.

## Why this is the worked long_horizon

- First **bimanual long-horizon** in RoboRSI (true parallelism)
- Built on `bicoord` backend (RoboTwin fork) — proves we can host new backends
- 4 objects = at least 4 atomic phases (pick + place per object), naturally
  decomposable into a plan

## Lifecycle (3 sub-skills)

| sub-skill | what it does |
|---|---|
| `execute/`        | LH task identity (wiki + intent). Execution runs through the 3-role triangle (LHPlanner → LHExecutor → LHReviewer), not this skill directly. |
| `progress_judge/` | Reads metadata; calls _lib.progress_score after each atomic phase, asks VLM "did this object end up on the right side?" |
| `posttrain/`      | Skeleton — would replay execution traces with sparse phase rewards into _lib.rl.pi0_posttrain on each participating atomic's policy. |

## Status

- execute/progress_judge wired
- posttrain skeleton (matches clean_table)
- Atomics: candidate atomics is a placeholder list; the real "pick_and_place_at_pixel"
  atomic is to-be-built. For the integration test, plan output structure is
  validated; per-atomic execution falls back to rollout zeroshot via base tools.
