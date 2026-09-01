---
name: libero_long
kind: task_family
domain: long_horizon
version: 0.1.0
description: Execute an ordered sequence of LIBERO manipulation sub-goals from current observations.
metadata:
  tags: [task-family, long-horizon, libero, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  runtime_status: shared_runner
  vlm_prompts:
    instruction: |
      Execute the runtime instruction as ordered visible sub-goals. Re-observe
      after every scene-changing action, preserve a confirmed hold until its
      placement is complete, and keep completed sub-goals intact while working
      on the next one.
    expected_on_success: |
      Every requested visible sub-goal is complete in the final scene.
---

# libero_long

Task family for the ten public LIBERO long-horizon tasks.
