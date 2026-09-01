---
name: libero_90_17
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Stack the middle black bowl on the back black bowl.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 17
    task_key: libero_90/17
  vlm_prompts:
    instruction: |
      Stack the middle black bowl on the back black bowl.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_17

- Benchmark task: `libero_90/17`
- Parent task family: `libero_pick_place`
- Visible instruction: Stack the middle black bowl on the back black bowl.
