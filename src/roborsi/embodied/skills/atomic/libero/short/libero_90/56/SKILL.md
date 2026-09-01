---
name: libero_90_56
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the butter and put it in the tray.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 56
    task_key: libero_90/56
  vlm_prompts:
    instruction: |
      Pick up the butter and put it in the tray.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_56

- Benchmark task: `libero_90/56`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the butter and put it in the tray.
