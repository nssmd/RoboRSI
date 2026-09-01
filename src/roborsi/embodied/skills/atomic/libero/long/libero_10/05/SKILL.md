---
name: libero_10_05
kind: atomic
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Pick up the book and place it in the back compartment of the caddy.
metadata:
  tags: [atomic, long, libero_10, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_10
    task_id: 5
    task_key: libero_10/5
  vlm_prompts:
    instruction: |
      Pick up the book and place it in the back compartment of the caddy.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_10_05

- Benchmark task: `libero_10/5`
- Parent task family: `libero_long`
- Visible instruction: Pick up the book and place it in the back compartment of the caddy.
