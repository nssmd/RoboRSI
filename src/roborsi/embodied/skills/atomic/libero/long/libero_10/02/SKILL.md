---
name: libero_10_02
kind: atomic
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Turn on the stove and put the moka pot on it.
metadata:
  tags: [atomic, long, libero_10, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_10
    task_id: 2
    task_key: libero_10/2
  vlm_prompts:
    instruction: |
      Turn on the stove and put the moka pot on it.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_10_02

- Benchmark task: `libero_10/2`
- Parent task family: `libero_long`
- Visible instruction: Turn on the stove and put the moka pot on it.
