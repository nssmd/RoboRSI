---
name: libero_90_23
kind: atomic
parent: libero_direct_manipulation
domain: direct_manipulation
version: 0.1.0
description: Close the bottom drawer of the cabinet and open the top drawer.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 23
    task_key: libero_90/23
  vlm_prompts:
    instruction: |
      Close the bottom drawer of the cabinet and open the top drawer.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_23

- Benchmark task: `libero_90/23`
- Parent task family: `libero_direct_manipulation`
- Visible instruction: Close the bottom drawer of the cabinet and open the top drawer.
