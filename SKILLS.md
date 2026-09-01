# RoboRSI Skill Schema

RoboRSI separates reusable robot knowledge into five layers:

```text
task_families/  reusable task decomposition and execution guidance
atomic/         one concrete benchmark task or user-requested capability
compound/       code-backed composition of Base Skills
executors/      execution mode attached to a Task Family
base/           one bounded robot or perception operation
```

Atomic Skills are grouped by backend:

```text
atomic/libero/short/<suite>/<task-id>/SKILL.md
atomic/libero/long/libero_10/<task-id>/SKILL.md
atomic/robotwin/<task-name>/SKILL.md
```

Every Atomic Skill must contain:

```yaml
name: unique_skill_name
kind: atomic
parent: task_family_name
domain: capability_category
version: 0.1.0
description: visible task description
metadata:
  backends: [backend_name]
  runtime_status: shared_runner
  benchmark:
    suite: benchmark_suite
    task_key: public_task_key
  vlm_prompts:
    instruction: visible task instruction
    expected_on_success: visible completion condition
```

## Ground-Truth Boundary

Skill documents may contain public task language and observations available to
the agent. They must not contain simulator predicates, object poses, hidden
regions, demonstration coordinates, reward values, or task-checker output.
Final success is assigned after execution by the simulator or real environment.

## Runtime Status

- `code-backed`: the skill has a local `policy.py`.
- `shared_runner`: the Task Family and backend execute the skill.
- `requires_robotwin_backend`: the public task profile is present, while its
  RoboTwin backend and Base Skill runtime are not yet included.
