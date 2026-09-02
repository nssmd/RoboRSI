---
name: read_task_wiki
kind: base
robot: robotwin
category: diagnostic
version: 0.1.0
description: |
  Returns the per-task wiki (markdown) which accumulates:
  1. Successful execution traces from past runs
  2. Failed execution traces + Reviewer diagnoses
  3. Key measurements (Reviewer-proposed, human-approved)

  Read-only. Use when you want to learn from prior runs of the same
  task before deciding your approach.

when_to_use: |
  - Start of an atomic when you don't know the best tool sequence yet.
  - After a failure when you want to see how others recovered.
  - When you need an embodiment-specific measurement (IK floor, link
    offset, etc.) that the Reviewer team has already characterized.

when_NOT_to_use: |
  - For live scene state (use describe_scene_actors instead).
  - For skill code (use read_skill_code instead).
  - To copy literal xyz from a prior run — the wiki is reference,
    not a live observation. Live obs always wins.

args:
  task: { type: string, required: true, description: "LH task name, e.g. 'handover_block_bicoord'. The wiki lives at ~/.roborsi/wiki/<task>.md." }

returns:
  ok: { type: boolean }
  task: { type: string }
  wiki_md: { type: string, description: "Full wiki markdown content (may be empty template if no entries yet)." }
  path: { type: string }
---

# Overview

Returns the on-disk wiki for a task. Wiki is automatically populated by
the harness (each atomic attempt appends its trace) and by Reviewers
(via approved measurement proposals).
