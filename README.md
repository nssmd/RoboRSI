# RoboRSI

RoboRSI is a general harness for evidence-driven robot self-evolution. It
connects robot backends, an agent and skill runtime, retained execution
evidence, code or model evolution, and evaluation-gated capability promotion.

This repository contains the public LIBERO short reference runtime. The
project website and media are maintained separately at
https://robo-rsi.com/.

## Core Features

1. **Autonomous skill evolution:** compose existing skills, diagnose visible
   failures, and author isolated candidate skills.
2. **Code and model consolidation:** turn verified execution into reusable
   compound code or predicate-gated training data.
3. **Evidence-gated adaptation:** keep task, seed, visible tool trace, cost,
   trajectory, and final environment verdict attached to every change.

## Integrity Contract

- Planner, Engineer, Reviewer, skills, and prompts cannot read hidden
  simulator state or the task-success predicate.
- Only the post-episode simulator predicate counts as success.
- Provider, transport, image, resource, and interrupted attempts are retained
  but excluded from task denominators.
- Successful task/seed pairs are protected from reruns.
- Candidate code is isolated and scanned before evaluation.
- Failed candidates, traces, trajectories, logs, and videos are preserved.

## Reported Evidence

The compact evidence bundle replays cumulative adaptive task coverage over the
120 LIBERO short tasks:

```text
Spatial   9/10
Object   10/10
Goal      9/10
LIBERO-90 67/90
Total    95/120
```

This is cross-release adaptive development coverage. It is not a frozen-policy
score, a single-release result, or conventional fixed-method Pass@10.

## Quick Start

Replay the retained result without a simulator or API:

```bash
./setup.sh --core-only
./roborsi results replay \
  --manifest evidence/adaptive-pass10-v1/manifest.json
```

Run a new adaptive campaign:

```bash
export OPENAI_API_KEY="..."
./setup.sh
./roborsi doctor
./roborsi eval libero-short --mode adaptive
```

`setup.sh` creates isolated environments, installs RoboRSI, checks out pinned
LIBERO and PyRoKi revisions when needed, writes `roborsi.yaml`, and runs
diagnostics. It is safe to rerun.

## Commands

| Command | Purpose |
| --- | --- |
| `./roborsi configure` | Write one non-secret YAML configuration |
| `./roborsi doctor` | Validate provider, simulator, paths, and services |
| `./roborsi services start` | Start and warm the isolated PyRoKi service |
| `./roborsi eval libero-short --mode adaptive` | Evaluate with gated skill evolution |
| `./roborsi eval libero-short --mode fixed` | Evaluate one immutable release |
| `./roborsi results replay --manifest ...` | Recompute task-level coverage |
| `./roborsi visualize skill-tree` | Render the interactive skill-evolution tree |
| `./roborsi dashboard` | Open the local result console |

## Skill-Tree Visualization

RoboRSI includes a standalone, offline skill-evolution viewer derived from the
retained 104-round case study. Internal run IDs and repair identifiers are
removed from the packaged storyboard.

```bash
./roborsi visualize skill-tree \
  --output artifacts/roborsi-skill-tree.html \
  --no-browser
```

The generated HTML contains its data, layout, and animation controls in one
file. A custom storyboard can be supplied with `--storyboard`.

## Configuration

```yaml
provider:
  model: responses/gpt-5.6-sol
  reasoning_effort: medium
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
evaluation:
  mode: adaptive
  seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
  task_count: 120
  tool_budget: 120
integrity:
  success_source: posthoc_simulator_predicate
  expose_task_checker: false
  action_success_latch: false
  allow_hidden_object_state: false
```

The API key remains in the named environment variable.

## Run Artifacts

```text
runs/<run-id>/
  manifest.json
  config.resolved.yaml
  state.json
  result.json
  journals/
  episodes/
  media/
  trajectories/
  proposals/
  candidate_overlays/
  releases/
```

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check src tests scripts
python scripts/release_check.py
python -m build
```

See [REPRODUCING.md](REPRODUCING.md) for protocol details and
[CONTRIBUTING.md](CONTRIBUTING.md) before changing a visible skill or
evaluation contract.

## Scope

The interfaces are backend-agnostic, but this public release ships only the
LIBERO short backend. It does not contain private endpoints, credentials,
operator launchers, raw internal logs, the website, or long-horizon work.

RoboRSI is licensed under Apache-2.0. LIBERO and PyRoKi retain their upstream
licenses. GraspGen is an optional external dependency with its own terms.
