---
name: libero_spatial_02
kind: atomic
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Pick up the black bowl from table center and place it on the plate.
metadata:
  tags: [atomic, short, libero_spatial, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  benchmark:
    suite: libero_spatial
    task_id: 2
    task_key: libero_spatial/2
  vlm_prompts:
    instruction: |
      Pick up the black bowl from table center and place it on the plate.
      Ground every object and relation from the current observations and preserve
      the requested order of operations.
    expected_on_success: |
      The complete visible instruction is satisfied in the final scene.
---

# libero_spatial_02

- Benchmark task: `libero_spatial/2`
- Parent task family: `libero_pick_place`
- Visible instruction: Pick up the black bowl from table center and place it on the plate.
