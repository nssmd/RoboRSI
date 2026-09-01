---
name: libero_90_37
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Put the white bowl to the right of the plate.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 37
    task_key: libero_90/37
  vlm_prompts:
    instruction: |
      Put the white bowl to the right of the plate.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_37

- Benchmark task: `libero_90/37`
- Parent task family: `libero_pick_place`
- Visible instruction: Put the white bowl to the right of the plate.
