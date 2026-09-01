---
name: libero_90_85
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the white mug and place it to the right of the caddy.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 85
    task_key: libero_90/85
  vlm_prompts:
    instruction: |
      Pick up the white mug and place it to the right of the caddy.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_85

- Benchmark task: `libero_90/85`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the white mug and place it to the right of the caddy.
