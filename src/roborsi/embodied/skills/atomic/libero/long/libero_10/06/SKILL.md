---
name: libero_10_06
kind: atomic
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Put the white mug on the plate and put the chocolate pudding to the right of the plate.
metadata:
  tags: [atomic, long, libero_10, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_10
    task_id: 6
    task_key: libero_10/6
  vlm_prompts:
    instruction: |
      Put the white mug on the plate and put the chocolate pudding to the right of the plate.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_10_06

- Benchmark task: `libero_10/6`
- Parent task family: `libero_long`
- Visible instruction: Put the white mug on the plate and put the chocolate pudding to the right of the plate.
