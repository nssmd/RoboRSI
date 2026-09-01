# RoboRSI

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](pyproject.toml)

RoboRSI is a robot-agent harness for turning execution experience into
reusable capability. It coordinates agent roles, hierarchical skills, retained
evidence, code-backed skill evolution, trajectory capture, and
evaluation-gated promotion.

This repository contains the public **LIBERO short reference runtime** and a
portable cross-backend skill catalog. It includes:

- a replayable evidence bundle for the reported `95/120` adaptive coverage;
- the fixed and adaptive 120-task evaluation protocols;
- the Planner, Engineer, Reviewer, supervisor, and worker runtime;
- the public LIBERO skill library and hidden-state firewall;
- representative RoboTwin Atomic Task profiles;
- a complete CLI for setup, execution, status, and replay;
- a local Web console for public evidence and live campaign results.

Project website: <https://robo-rsi.com/>

## Start Here

Choose the path that matches what you want to verify.

| Goal | API key | Simulator | GPU | Command |
| --- | --- | --- | --- | --- |
| Replay the reported result | No | No | No | `./reproduce.sh` |
| Inspect public evidence in a browser | No | No | No | `./roborsi web --public` |
| Inspect the published skill catalog | No | No | No | `./roborsi skills list` |
| Inspect the latest local campaign | No | No | No | `./roborsi status` |
| Validate a full configuration | Yes | Yes | Recommended | `./roborsi doctor` |
| Run a new 120-task campaign | Yes | Yes | Recommended | `./roborsi eval libero-short` |

### 1. Replay the public evidence

This is the fastest end-to-end check. It does not call a model or launch a
simulator.

```bash
git clone https://github.com/nssmd/RoboRSI.git
cd RoboRSI

./reproduce.sh
```

The script creates a core virtual environment, replays the packaged evidence,
and writes:

```text
artifacts/reproduction/replay.json
artifacts/reproduction/dashboard.html
```

The equivalent manual commands are:

```bash
./setup.sh --core-only
./roborsi results replay \
  --manifest evidence/adaptive-pass10-v1/manifest.json \
  --json artifacts/replay.json
```

Expected headline:

```text
Spatial    9/10
Object    10/10
Goal       9/10
LIBERO-90 67/90
Total     95/120
```

Generate a self-contained Web report:

```bash
./roborsi web \
  --result artifacts/replay.json \
  --output artifacts/dashboard.html \
  --no-browser
```

Or open the local Web console:

```bash
./roborsi web --public
```

### 2. Run the LIBERO reference runtime

Requirements:

- Linux;
- Python `3.10`, `3.11`, or `3.12`;
- Git;
- an OpenAI-compatible Responses endpoint with access to
  `gpt-5.6-sol`;
- a GPU is strongly recommended for practical simulation throughput.

```bash
export OPENAI_API_KEY="..."

./setup.sh
./roborsi eval libero-short --mode adaptive --dry-run
./roborsi doctor
./roborsi eval libero-short --mode adaptive
./roborsi status
./roborsi web
```

The full setup creates isolated environments, checks out pinned LIBERO and
PyRoKi revisions, writes `roborsi.yaml`, starts the motion-planning service,
and runs offline diagnostics. It is safe to rerun.

## CLI And Web Console

The CLI is the primary control surface:

```bash
./roborsi runs list
./roborsi status
./roborsi status <run-id> --json artifacts/status.json
```

`status` selects the latest campaign when no run is supplied. It reports
campaign state, completed passes, task-level coverage, verdict counts, Token
usage, VLM calls, elapsed episode time, and the run directory.

The Web console selects the latest local campaign when available:

```bash
./roborsi web
./roborsi web --run <run-id>
./roborsi web --public
```

While a campaign is running, the served page reloads its retained state every
15 seconds. To create a standalone HTML snapshot:

```bash
./roborsi web --run <run-id> --output artifacts/run.html --no-browser
```

The Web console shows cumulative coverage, suite results, episode verdicts,
protocol fields, resource totals, and release history. It reads local campaign
artifacts without sending them to an external service.

## Skill Catalog

The package currently ships 35 Base Skills, 182 task-level Atomic Skills,
11 Task Families, two Executors, and two code-backed Compound Skills. The
Atomic layer contains all 130 public LIBERO task profiles and 52 RoboTwin task
profiles.

```bash
./roborsi skills list
./roborsi skills list --category atomic --backend robotwin
./roborsi skills list --category atomic --backend libero
./roborsi skills show lift_pot
```

The ten `libero_10` profiles form the long-horizon task set. LIBERO Atomic
profiles contain only official public language instructions; no simulator
predicate or hidden scene state is stored in a Skill. RoboTwin profiles are
labeled `requires_robotwin_backend` until that runtime is included.

See [SKILLS.md](SKILLS.md) for the directory hierarchy and required frontmatter.

## How RoboRSI Is Organized

```text
Human steering
  objectives · values · expertise · safety boundaries
                         |
                         v
Manager -> Planner -> Engineer -> LIBERO / robot backend
   ^          |           |                 |
   |          v           v                 v
   +------ Reviewer <- plans, tool traces, video, trajectories, verdicts
                         |
                         v
Task Family -> Atomic Task -> Base Skill
                         |
             validated code-backed skill
                         |
                         +----> next task / next pass
```

The public runtime keeps two responsibilities separate:

- **Multi-Agent operation** reduces low-level human work by assigning task
  management, planning, execution, diagnosis, and revision to explicit roles.
- **Top-down Skill Refinement** gives the agent a bounded hierarchy in which
  failures can be attributed and repaired locally instead of rewriting the
  whole system.

Execution trajectories are retained in a training-ready form so a separate
policy-training pipeline can consume them without changing the evaluation
contract.

## Evaluation Modes

| Mode | Skills may evolve? | Release identity | Reported metric |
| --- | --- | --- | --- |
| `adaptive` | Yes, after validation | May advance across passes | Task-level adaptive Pass@10 |
| `fixed` | No | One immutable release | Task-level fixed Pass@10 |

For both modes:

- the catalog contains exactly 120 LIBERO short tasks;
- ordered seeds are `0..9`;
- any final simulator-confirmed success solves a task once;
- successful task/seed pairs are protected from reruns;
- provider, transport, image, resource, and interrupted attempts are retained
  but excluded from task denominators;
- hidden simulator state and task-success predicates remain outside all
  agent-visible prompts, plans, skills, and tool outputs.

## Configuration

`./setup.sh` writes one canonical `roborsi.yaml`. To create or update it
manually:

```bash
./roborsi configure \
  --output roborsi.yaml \
  --gpus auto \
  --workers 8 \
  --yes
```

The important fields are:

```yaml
provider:
  model: responses/gpt-5.6-sol
  reasoning_effort: medium
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY

simulator:
  root: .deps/LIBERO
  mujoco_gl: egl
  controller: JOINT_POSITION
  image_size: 512
  horizon: 5000

runtime:
  results_root: runs
  workers: 8
  gpu_devices: [0]

evaluation:
  mode: adaptive
  seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
  task_count: 120
  tool_budget: 120
```

Only the environment-variable name is stored. The secret itself is never
written to the YAML file.

## Command Reference

| Command | Purpose |
| --- | --- |
| `./roborsi configure` | Write the canonical non-secret configuration |
| `./roborsi doctor` | Check provider, simulator, paths, and services |
| `./roborsi doctor --offline --replay-only` | Check only local replay requirements |
| `./roborsi services start` | Start and warm the isolated PyRoKi service |
| `./roborsi services status` | Check the managed PyRoKi process and port |
| `./roborsi services stop` | Stop PyRoKi and retain its service record |
| `./roborsi eval libero-short --mode adaptive` | Run evaluation with gated skill evolution |
| `./roborsi eval libero-short --mode fixed` | Run one immutable release |
| `./roborsi eval libero-short --dry-run` | Validate the campaign shape without launching |
| `./roborsi results replay` | Recompute task-level coverage from retained evidence |
| `./roborsi runs list` | List local campaigns newest first |
| `./roborsi skills list` | List Base and Atomic Skills by backend |
| `./roborsi skills show NAME` | Inspect one skill profile |
| `./roborsi status [RUN]` | Inspect the latest or selected campaign |
| `./roborsi web` | Open the latest campaign in the local Web console |
| `./roborsi web --public` | Open the packaged public evidence |
| `./roborsi web --output FILE` | Write a standalone Web report |
| `./roborsi visualize skill-tree` | Write the interactive skill-evolution viewer |

## Run Artifacts

Each new campaign writes an append-only run directory:

```text
runs/<run-id>/
  manifest.json
  config.resolved.yaml
  state.json
  result.json
  supervisor.log
  journals/
  logs/
  media/
  traces/
  trajectories/
  proposals/
  candidate_overlays/
```

Failed runs and rejected candidates are retained. Adaptive proposals are loaded
through an isolated overlay and cannot overwrite the active release before
validation.

## Reported Evidence

The packaged evidence bundle replays cumulative coverage over the 120 LIBERO
short tasks:

```text
Spatial   9/10
Object   10/10
Goal      9/10
LIBERO-90 67/90
Total    95/120
```

This is **cross-release adaptive development coverage**. It is not a
single-release score or a conventional frozen-policy Pass@10 result. The
compact bundle retains one canonical simulator-success row per solved task; it
does not contain every failed or infrastructure attempt and therefore cannot
reconstruct total campaign Token or wall-clock spend.

See [REPRODUCING.md](REPRODUCING.md) for the exact protocols and comparison
rules.

## Skill Tree

Render the packaged 104-round case study:

```bash
./roborsi visualize skill-tree \
  --output artifacts/roborsi-skill-tree.html \
  --no-browser
```

The output is a standalone HTML file with embedded data and interaction
controls. Run-local metadata is excluded from the packaged storyboard.

## Optional GraspGen Service

The default runtime can produce a bounded geometric top-down grasp candidate
without GraspGen. Historical releases also used an external GraspGen service
for some object shapes. GraspGen is not installed automatically and retains its
own non-commercial terms. Setup details are in
[REPRODUCING.md](REPRODUCING.md).

## Repository Layout

```text
src/roborsi/             runtime, skills, CLI, evaluation, and evidence tooling
scripts/                 setup helpers, services, and release checks
evidence/                compact replayable result bundle
tests/                   public-contract and runtime tests
reproduce.sh             one-command public-result replay
```

The project website and unrelated research assets are maintained separately.

## Development

Core-only checks:

```bash
./setup.sh --core-only --dev
source .venv/bin/activate

pytest -q -m "not runtime"
ruff check src/roborsi/libero tests scripts
python scripts/release_check.py
roborsi results replay --json /tmp/roborsi-replay.json
roborsi web \
  --result /tmp/roborsi-replay.json \
  --output /tmp/roborsi-dashboard.html \
  --no-browser
python -m build
```

Complete runtime checks require `./setup.sh --dev`; after the runtime
dependencies and pinned simulator checkout are installed, run:

```bash
pytest -q
python scripts/check_libero_gt_leak.py
```

CI uses this complete test path.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing a visible skill,
evaluation contract, or promotion rule. Security and secret-handling guidance
is in [SECURITY.md](SECURITY.md).

## Citation And License

Citation metadata is provided in [CITATION.cff](CITATION.cff).

RoboRSI is licensed under Apache-2.0. LIBERO, PyRoKi, and optional external
services retain their upstream licenses and terms.
