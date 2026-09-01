# RoboRSI Skill Catalog

The package ships 283 public Skill documents:

- 86 Base Skills: 35 LIBERO and 51 RoboTwin contracts
- 182 Atomic Skills: 120 LIBERO short, 10 LIBERO long-horizon, and 52 RoboTwin
- 11 Task Families
- 2 Executors
- 2 code-backed Compound Skills

Repeated episodes, seeds, videos, and traces are evidence for a Skill. They are
not counted as additional Skills.

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

Base Skills use backend namespaces because the same operation can have
different implementations:

```text
base/<operation>/libero/SKILL.md
base/<operation>/robotwin/SKILL.md
```

Use qualified references when a name exists in more than one backend:

```bash
./roborsi skills show libero/grasp_object
./roborsi skills show robotwin/grasp_object
```

Every Skill contains `name`, `kind`, `version`, `description`,
`metadata.backends`, and `metadata.runtime_status`. An Atomic Skill additionally
contains:

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

Base Skills additionally contain a backend namespace and structured call
contract:

```yaml
name: grasp_object
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Parameterized operation description.
args:
  object: {type: string, required: true}
returns:
  ok: bool
metadata:
  backends: [robotwin]
  runtime_status: requires_robotwin_backend
```

## Agent-Visible Boundary

Skill documents may contain public task language and observations available to
the Agent. They must not contain task-checker calls, hidden simulator state,
fixed demonstration coordinates, task-specific test seeds, or reward labels.
Final environment adjudication remains outside the Agent-visible Skill catalog.

Validate the complete catalog with:

```bash
./roborsi skills validate
```

## Runtime Status

- `code-backed`: the skill has a local `policy.py`.
- `shared_runner`: the Task Family and backend execute the skill.
- `requires_robotwin_backend`: the public task or Base Skill contract is
  present, while the RoboTwin runtime is not included in this release.
