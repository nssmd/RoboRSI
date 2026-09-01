---
name: libero_90_64
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Stack the right bowl on the left bowl and place them in the tray.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 64
    task_key: libero_90/64
  vlm_prompts:
    instruction: |
      Stack the right bowl on the left bowl and place them in the tray.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_64

- Benchmark task: `libero_90/64`
- Parent task family: `libero_pick_place`
- Visible instruction: Stack the right bowl on the left bowl and place them in the tray.
