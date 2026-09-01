---
name: libero_90_04
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Put the butter at the front in the top drawer of the cabinet and close it.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 4
    task_key: libero_90/4
  vlm_prompts:
    instruction: |
      Put the butter at the front in the top drawer of the cabinet and close it.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_04

- Benchmark task: `libero_90/4`
- Parent task family: `libero_pick_place`
- Visible instruction: Put the butter at the front in the top drawer of the cabinet and close it.
