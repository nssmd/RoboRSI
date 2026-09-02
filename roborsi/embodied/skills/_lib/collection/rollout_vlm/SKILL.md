---
name: rollout_vlm
kind: lifecycle
lifecycle: collection
version: 0.1.0
description: Zero-shot data collection — VLM drives modular perception/geometry/control tools to complete a task. No pre-trained policy required. Falls back to 'did-not-finish' when the VLM can't figure out a step.
metadata:
  tags: [data, vlm, zero-shot, rollout]
  related_skills: [expert_replay, progress_score]
params:
  task:         { type: string, required: true }
  backend:      { type: string, default: robotwin }
  episodes:     { type: int,    default: 1 }
  seed_start:   { type: int,    default: 0 }
  model:        { type: string, description: "VLM model id (litellm). Defaults to ROBORSI_VLM_MODEL." }
  tool_budget:  { type: int,    default: 30, description: "Max VLM calls per episode; abort after." }
  skill_label:  { type: string, description: "DataStore label; defaults to <task>." }
---

# rollout_vlm — VLM-orchestrated zero-shot collection

## Overview

For tasks **without** a scripted expert, have a VLM (Claude / Qwen-VL /
Molmo) drive the robot: it reads the current RGB, picks a tool call
(perceive / geometry / control / image_edit), inspects the result,
decides the next tool. Loop until success or `tool_budget` exhausted.

This is the Rollout recipe, specialised for RoboRSI: instead of
hard-coding the module set inside the VLM prompt, we expose RoboRSI's
own Toolkit (PerceiveToolGroup + future GeometryToolGroup +
SimActionToolGroup) and let the VLM call them.

## When to use

- A new task in sim with no expert yet.
- You want to generate **diverse** bootstrap data (VLM makes different
  mistakes than a scripted expert → broader coverage).
- You have API budget (cheap models still cost per episode).

## When NOT to use

- You already have a scripted expert → `expert_replay` is cheaper and
  more reliable.
- The task requires sub-20ms reaction (VLM latency kills it).

## Phases

1. Load task SKILL.md (task kind); read `vlm_prompts.*` from its
   frontmatter. These anchor the Rollout prompt with task-specific
   object names + success criteria.
2. Reset env; snapshot first obs → JPEG.
3. Loop:
   1. Feed frame + tool catalogue + last tool result to VLM.
   2. Parse tool call; execute via Toolkit.
   3. Break if VLM answers `{"done": true}` or `tool_budget` hit.
4. Ask VLM for a final judge score (did we actually succeed?). Cross-check
   with `env.check_success` if available.
5. `DataStore().write(rollout, skill=skill_label, extra_meta={"collector": "rollout_vlm"})`.

## Success criteria

- Either `env.check_success() == True` AND VLM judge agrees, OR VLM
  explicitly signals done and success predicate agrees.
- Tool calls are recorded step-by-step in `meta.json.tool_trace` for
  later inspection.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| VLM loops on the same tool | Prompt lacks progress anchor | Add `progress_score` skill as phase-gate (see judging/progress_score) |
| Tool call JSON malformed | Model warm-up or bad seed | Retry once with temperature 0.1 bump |
| Budget hit every episode | Task really needs expert or learned policy | Switch to expert_replay or train π₀ |

## Implementation status

**SKELETON.** The executable `policy.py` wires the VLM tool loop only
when the Toolkit exposes a sim-action tool group. Current Toolkit has
Perceive only; the GeometryToolGroup and SimActionToolGroup arrive in
the next slice (Planner → Runtime coupling). Until then `policy.py`
raises `NotImplementedError` with a clear message.

## Why

Rollout proved VLM + modular tools can cover 6/7 tabletop benchmarks
zero-shot, outperforming π₀ and Gemini Robotics. For RoboRSI that
means: a new task gets a usable data source immediately without human
teleoperation or hand-written experts. Once enough episodes land, π₀
training takes over and this skill retires to "explore unknown edges".
