---
name: libero_90_87
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the book on the left and place it on top of the shelf.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 87
    task_key: libero_90/87
  vlm_prompts:
    instruction: |
      Pick up the book on the left and place it on top of the shelf.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_87

- Benchmark task: `libero_90/87`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the book on the left and place it on top of the shelf.
