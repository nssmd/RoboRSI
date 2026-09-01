---
name: stack_bowls_bicoord
kind: atomic
parent: robotwin_bimanual
domain: bimanual
version: 0.1.0
description: Coordinate two arms to nest one bowl into another.
metadata:
  tags: [atomic, bimanual, stacking, robotwin]
  embodiments: [aloha-agilex]
  backends: [robotwin]
  runtime_status: requires_robotwin_backend
  benchmark:
    suite: robotwin
    task_key: stack_bowls_bicoord
  vlm_prompts:
    instruction: Assign the bowls to safe arm workspaces, stabilize the lower bowl, align the upper bowl by its rim, and lower it into a centered nested pose.
    expected_on_success: The bowls are visibly nested in one stable stack.
---

# stack_bowls_bicoord

Task profile for coordinated bowl stacking.
