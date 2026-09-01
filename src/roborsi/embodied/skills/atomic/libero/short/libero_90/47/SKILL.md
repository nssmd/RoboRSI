---
name: libero_90_47
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the cream cheese box and put it in the basket.
metadata:
  tags: [atomic, short, libero_90, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_90
    task_id: 47
    task_key: libero_90/47
  vlm_prompts:
    instruction: |
      Pick up the cream cheese box and put it in the basket.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_90_47

- Benchmark task: `libero_90/47`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the cream cheese box and put it in the basket.
