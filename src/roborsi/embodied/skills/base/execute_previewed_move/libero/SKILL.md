---
name: execute_previewed_move
kind: base
robot: libero
category: control
version: 0.1.0
description: Execute exactly one previously previewed LIBERO move after checking that the observation
  generation and robot pose are unchanged.
args:
  preview_id:
    type: string
    required: true
    description: One-time token from preview_move_to_pose.
returns:
  ok: bool
  reached: bool
  preview_consumed: bool
when_to_use: |
  Immediately after inspecting a preview_move_to_pose result. The token is
  consumed before motion and the tool attaches a fresh head observation after
  execution.
when_NOT_to_use: |
  Never replay a token or use it after another observation/action. Preview
  reachability does not establish grasp, placement, or task completion.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# execute_previewed_move

One-shot execution of an IK-checked preview, followed by fresh visual feedback.
