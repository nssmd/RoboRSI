---
name: libero_goal_03
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Open the top drawer and put the bowl inside.
metadata:
  tags: [atomic, short, libero_goal, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_goal
    task_id: 3
    task_key: libero_goal/3
  vlm_prompts:
    instruction: |
      Open the top drawer and put the bowl inside.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_goal_03

- Benchmark task: `libero_goal/3`
- Parent task family: `libero_pick_place`
- Visible instruction: Open the top drawer and put the bowl inside.
