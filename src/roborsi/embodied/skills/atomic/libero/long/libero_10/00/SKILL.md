---
name: libero_10_00
kind: atomic
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Put both the alphabet soup and the tomato sauce in the basket.
metadata:
  tags: [atomic, long, libero_10, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_10
    task_id: 0
    task_key: libero_10/0
  vlm_prompts:
    instruction: |
      Put both the alphabet soup and the tomato sauce in the basket.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_10_00

- Benchmark task: `libero_10/0`
- Parent task family: `libero_long`
- Visible instruction: Put both the alphabet soup and the tomato sauce in the basket.
