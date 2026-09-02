---
name: progress_score
kind: lifecycle
lifecycle: judging
version: 0.1.0
description: VLM-based phase-gate. Given a before/after image pair for a named phase, return structured {done, score, reason}. Drives continue/retry/replan decisions in the Plan-Act-Judge loop.
metadata:
  tags: [judging, vlm, phase-gate]
  related_skills: [rollout_vlm]
params:
  phase:       { type: string, required: true, description: "Phase label, e.g. 'grasp-hammer'." }
  image_path:  { type: string, required: true }
  expected:    { type: string, required: true, description: "What success looks like in plain English." }
  model:       { type: string, description: "Override VLM model." }
---

# progress_score — VLM phase-gate

## Overview

After each phase of a skill, snap the scene and ask the VLM:

> **Phase**: grasp-hammer
> **Expected**: The hammer is held by the right gripper, off the table.
> **Image**: <scene.jpg>
>
> Answer in JSON: `{"done": bool, "score": 0-1, "reason": str}`.

This skill encapsulates that call. Consumers (planner, skill runtime)
use `done` to decide continue, `score` for logging / ranking, `reason`
for human debugging.

## Why as a skill

The same phase-gate logic shows up in (a) VLM-orchestrated execution
(rollout_vlm), (b) long-horizon plan execution (LH triangle), and (c)
post-training evaluation. One implementation, many call sites.

## Output

```json
{
  "phase": "grasp-hammer",
  "done": true,
  "score": 0.85,
  "reason": "Right gripper holds the hammer handle; no contact with the table."
}
```

## Implementation

Uses the same litellm channel as `PerceiveToolGroup`. Default model is
`anthropic/claude-sonnet-4-6`, overridable via `ROBORSI_JUDGE_MODEL`.
Fast-path: swap in Qwen2.5-VL-7B locally when we want 2 Hz polling à la
Rollout's VLM monitor.
