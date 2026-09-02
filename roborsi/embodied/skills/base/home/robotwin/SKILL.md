---
name: home
kind: base
robot: robotwin
version: 0.1.0
description: Return both arms to their home (factory rest) joint configuration.
metadata:
  tags: [base, motion, reset, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {}
    pass_criteria:
      kind: move_completes
      min_seeds_passing: 1
params:
  env: { type: object, required: true }
returns:
  ok: "bool"
---

# home · RoboTwin

Return arms to their factory home pose. Used as the first step of `reset_success` and `reset_failure`.

## Implementation

Calls `env._impl.robot.move_to_homestate()` which is what RoboTwin's own `_init_task_env_` calls right after `setup_scene`.
