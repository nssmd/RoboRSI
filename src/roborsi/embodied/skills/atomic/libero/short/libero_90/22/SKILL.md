---
name: libero_90_22
kind: atomic
parent: libero_direct_manipulation
domain: direct_manipulation
version: 0.1.0
description: Close the bottom drawer of the cabinet.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 22
    task_key: libero_90/22
  vlm_prompts:
    instruction: |
      Close the bottom drawer of the cabinet.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_22

- Benchmark task: `libero_90/22`
- Parent task family: `libero_direct_manipulation`
- Visible instruction: Close the bottom drawer of the cabinet.
