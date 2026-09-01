---
name: libero_object_09
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the orange juice and place it in the basket.
metadata:
  tags: [atomic, short, libero_object, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_object
    task_id: 9
    task_key: libero_object/9
  vlm_prompts:
    instruction: |
      Pick up the orange juice and place it in the basket.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_object_09

- Benchmark task: `libero_object/9`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the orange juice and place it in the basket.
