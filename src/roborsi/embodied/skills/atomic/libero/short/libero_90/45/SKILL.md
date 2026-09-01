---
name: libero_90_45
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Turn on the stove and put the frying pan on it.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 45
    task_key: libero_90/45
  vlm_prompts:
    instruction: |
      Turn on the stove and put the frying pan on it.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_45

- Benchmark task: `libero_90/45`
- Parent task family: `libero_pick_place`
- Visible instruction: Turn on the stove and put the frying pan on it.
