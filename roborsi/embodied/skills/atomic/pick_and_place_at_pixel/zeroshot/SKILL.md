---
name: pick_and_place_at_pixel.zeroshot
kind: atomic_subskill
parent: pick_and_place_at_pixel
phase: zeroshot
version: 0.1.0
description: VLM drives base/robotwin tools to pick a named object and release it onto a named target zone. Captures dense per-tick obs+qpos.
metadata:
  tags: [zeroshot, vlm, sim]
  base_tools: [capture_image, find_pixel, move_to_pixel, set_gripper, home]
params:
  env:           { type: object, required: true, description: "Active SimEnv (passed by long_horizon executor)." }
  source_object: { type: string, required: true }
  target_zone:   { type: string, required: true }
  task_name:     { type: string, default: pick_and_place_at_pixel, description: "Logical task label for DataStore." }
  tool_budget:   { type: int,    default: 18 }
  model:         { type: string }
  workdir:       { type: string }
  seed:          { type: int }
returns:
  success: "bool"
  outcome: "str"
  rollout: "SimRollout dict"
---

# pick_and_place_at_pixel / zeroshot

Drives the same rollout VLM loop used by click_bell, but with a 2-phase
prompt: pick from `source_object` pixel, then place on `target_zone` pixel.
The env handle is **passed in** by the long_horizon executor (not spawned
here) so that successive atomics share the same scene.
