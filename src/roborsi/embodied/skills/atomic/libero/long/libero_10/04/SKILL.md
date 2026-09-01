---
name: libero_10_04
kind: atomic
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Put the white mug on the left plate and put the yellow and white mug on the right plate.
metadata:
  tags: [atomic, long, libero_10, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_10
    task_id: 4
    task_key: libero_10/4
  vlm_prompts:
    instruction: |
      Put the white mug on the left plate and put the yellow and white mug on the right plate.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_10_04

- Benchmark task: `libero_10/4`
- Parent task family: `libero_long`
- Visible instruction: Put the white mug on the left plate and put the yellow and white mug on the right plate.
