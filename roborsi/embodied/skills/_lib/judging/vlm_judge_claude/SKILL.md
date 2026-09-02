---
name: vlm_judge_claude
kind: lib
domain: judging
version: 0.1.0
description: VLM-as-judge using a separate Claude CLI process. Spawns `claude -p --bare --output-format json` with image paths in the prompt; the subprocess Reads the images and returns a strict JSON success verdict. Used by atomic.judge sub-skills and long_horizon.progress_judge to replace sim-side success_predicate.
metadata:
  tags: [judging, vlm, claude, no-priv-state]
params:
  criterion:        { type: string, required: true, description: "what counts as success in plain English" }
  images:           { type: list,   required: true, description: "absolute image paths the judge should look at" }
  context:          { type: string, default: "",    description: "optional extra context (atomic args, scene description)" }
  model:            { type: string, default: "",    description: "optional model override; empty = claude CLI default" }
  timeout_s:        { type: int,    default: 90 }
returns:
  success: "bool"
  reason:  "str"
  raw:     "str (subprocess stdout)"
---

# vlm_judge_claude

Atomic / long_horizon-agnostic. Caller passes a one-line success criterion and
N image paths; we spawn Claude CLI in print mode, prompt it to Read each image
and reply ONLY with `{"success": bool, "reason": "..."}`.

Used everywhere we previously relied on sim-privileged `check_success`. Same
contract on real hardware (just images, no GT poses).
