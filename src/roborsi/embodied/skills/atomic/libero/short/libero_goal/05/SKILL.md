---
name: libero_goal_05
kind: atomic
parent: libero_direct_manipulation
domain: direct_manipulation
version: 0.1.0
description: Push the plate to the front of the stove.
metadata:
  tags: [atomic, short, libero_goal, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_goal
    task_id: 5
    task_key: libero_goal/5
  vlm_prompts:
    instruction: |
      Push the plate to the front of the stove.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_goal_05

- Benchmark task: `libero_goal/5`
- Parent task family: `libero_direct_manipulation`
- Visible instruction: Push the plate to the front of the stove.
