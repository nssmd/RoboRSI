---
name: execute_with_pi05
kind: base
robot: robotwin
category: policy
version: 0.1.0
description: Delegate a sub-task to a pretrained pi0.5 VLA. Rolls out the policy on the live env with a natural-language instruction.
args:
  instruction: { type: string, required: true }
  max_steps:   { type: int, default: 200 }
returns:
  ok: bool
  steps: int
  outcome: str
when_to_use: |
  When high-level reasoning + tool composition can't reliably express the
  fine-motor action — e.g. "rotate cube purple side up", "fold the corner".
  Generalist VLAs handle these; you just say what to do.
  Requires ROBORSI_PI05_CKPT to point at a lerobot-format pi0.5 ckpt.
metadata:
  harness:
    skip_harness: true
    skip_reason: "requires pi0.5 VLA server"
---
