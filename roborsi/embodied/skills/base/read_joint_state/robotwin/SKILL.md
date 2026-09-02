---
name: read_joint_state
kind: base
robot: robotwin
version: 0.1.0
description: Read the current joint angles + gripper widths for both arms.
metadata:
  tags: [base, perception, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: []
      min_seeds_passing: 1
params:
  env: { type: object, required: true }
returns:
  left_arm: "list[float]"
  left_gripper: "float"
  right_arm: "list[float]"
  right_gripper: "float"
---

# read_joint_state · RoboTwin

Pull the current `qpos` for both arms + gripper widths. Used by:

- Atomic `eval/` to log per-step state into the trajectory
- `train/` to construct the action label (next-step qpos)
- VLM tool calls when "where am I right now?" matters
