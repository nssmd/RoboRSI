# Evolve and Frozen Modes

RoboRSI has two runtime modes:

- `evolve`: normal operation; the Reviewer may propose and the Manager may
  publish skill changes, and episodes update the task wiki and plan archive.
- `frozen`: evolution is disabled. No skill, task wiki, plan archive,
  proposal or training data is written. The Planner and Reviewer still run
  as their persistent role sessions, as in evolve mode. (`eval` is accepted
  as a legacy name.)

## Recommended setup

The settings below are the ones we use for the LIBERO runs; they are a
working starting point, not a requirement.

| Item | Recommended | Notes |
|---|---|---|
| Model | `gpt-5.6-sol` over the Responses API (`ROBORSI_OPENAI_TRANSPORT=responses`), reasoning effort `medium` | The paper uses this model for every role. Planner and Reviewer run as `codex` sessions, so `codex` must reach the same endpoint. |
| GPUs | One GPU for GraspGen and PyRoKi; the episode workers on the others with `ROBORSI_PERCEPTION_DEVICE=spread` | Each worker renders the scene and loads its perception models (about 5 GB); GraspGen uses about 5 GB. We run 8-20 workers over seven 96 GB H20 GPUs. |
| GraspGen | Required for results comparable to the paper | Without it grasps fall back to an analytic planner and success drops. |
| Parallel episodes | `ROBORSI_EVAL_WORKERS=8` | More workers mainly hit the model's rate limit; with 20 workers on a shared deployment many episodes were rate-limited. |
| Tool budget | `ROBORSI_TOOL_BUDGET=80` (paper), `ROBORSI_ROUND_TOOL_BUDGET=40` | A per-round budget leaves room for the Reviewer's corrections; we also use 120 per episode. |
| Rounds | `ROBORSI_EPISODE_MAX_ROUNDS=6` (default) | |
| State | A fresh `ROBORSI_HOME` per campaign | Wiki, sessions and review queues of earlier campaigns would otherwise carry over. |
| Time | LIBERO, 30 tasks x 5 episodes, 8 workers: about 12 hours | Median episode about 22 minutes, 90th percentile about 50 minutes. |

Before a long run, run one episode (`--task libero_spatial/1 --pass-at 1`)
and read its workspace under `ROBORSI_HOME/workspaces/`: `plan.md`, the
round traces and the `initial_view_*` / `final_view_*` frames show what the
Planner, Engineer and Reviewer worked from.

## Run an atomic evaluation

```bash
roborsi eval <atomic-task> --seeds 5 --seed-start 0
```

LIBERO example:

```bash
roborsi eval libero_pick_place \
  --backend libero \
  --sim-task libero_object/0 \
  --seeds 5 \
  --tool-budget 40
```

The command runs Planner, Engineer, and Reviewer for each seed. The simulator's
post-episode verdict remains the only success label.

Every invocation writes a machine-readable campaign manifest under
`~/.roborsi/evals/manifests/`. Success rate is computed only over seeds that
received a final simulator verdict; provider, backend, transport, and other
infrastructure errors are reported separately as `infra_count`. Code defects
are reported as `implementation_error_count`, not folded into task failures.

Role models can be pinned independently:

```bash
roborsi eval libero_pick_place \
  --backend libero \
  --sim-task libero_object/0 \
  --planner-model anthropic/claude-opus-4-8 \
  --engineer-model anthropic/claude-opus-4-8 \
  --reviewer-model anthropic/claude-opus-4-8
```

## What eval records

- the per-run `plan.md`, `summary.md`, and `review.md`;
- tool calls and visible execution trace;
- timing and final outcome in `trace.db`;
- prompt, completion, and total tokens; metered/unmetered VLM calls; role and
  total VLM wall time;
- successful and failed evaluation video evidence when frames are available;
- `run_mode=eval` on each run row.

## What eval freezes

- no persistent Planner, Reviewer, or Manager session update;
- no `register_skill`;
- no skill or patch proposal;
- no proposal validation or application;
- no task-wiki append;
- no persistent-plan promotion;
- no successful-plan or SkillSelector history update;
- no compound-policy promotion;
- no training-data write under `~/.roborsi/data`.
- no arbitrary outer-agent Python execution or non-atomic skill execution.

Direct skill runs that use `DataStore` are redirected to
`~/.roborsi/evals/` while frozen mode is active.

## Run LIBERO short task-level pass@K

```bash
roborsi eval-suite \
  --backend libero-pro \
  --pass-at 5 \
  --seed-start 0 \
  --workers 4 \
  --tool-budget 40 \
  --out ~/.roborsi/evals/libero-pro-pass5
```

Released code-backed compound skills are always exposed.

The suite runner:

- enumerates only LIBERO short tasks and excludes long-horizon suites;
- gives each task at most `K` seeds and stops scheduling it after its first
  simulator-confirmed success;
- keeps an append-only episode journal;
- retries infrastructure interruptions without treating them as task failures;
- reports task-level pass@K overall and by `spatial`, `object`, `goal`, `task`,
  `swap`, and `lan` group;
- preserves successful task/seed rows across resumes.

The output directory contains:

```text
campaign.json   exact task panel, seeds, role models, budget, retries, runtime
episodes.jsonl  append-only success, failure, infrastructure, and bug attempts
summary.json    task-level pass@K and group breakdowns
```

Reuse the same `--out` directory to resume. RoboRSI refuses the resume if the
task list, seed range, backend, role models, tool budget, worker count, or retry
policy differs from `campaign.json`.

## Reproduce a current-release LIBERO-PRO Pass 1

`scripts/reproduce_libero_pro.sh` performs every step below. To run them by
hand, configure the official LIBERO-PRO BDDL and init-state directories, start
PyRoKi (and optionally GraspGen), then run:

```bash
export OPENAI_API_KEY=...                 # OpenAI-compatible endpoint key
export OPENAI_BASE_URL=...                # optional; default api.openai.com
export ROBORSI_OPENAI_TRANSPORT=responses # or chat_completions
ROBORSI_EVAL_MODEL=<model id> \
ROBORSI_EVAL_WORKERS=8 \
scripts/run_libero_pro_matched_pass1.sh \
  ~/.roborsi/evals/suites/libero-pro-matched-pass1
```

Environment used by the reproduction scripts:

| Variable | Meaning |
|---|---|
| `ROBORSI_EVAL_MODEL` | Model id for the Planner, Engineer and Reviewer (required). |
| `ROBORSI_OPENAI_TRANSPORT` | `responses` (default in the scripts) or `chat_completions`. |
| `ROBORSI_LIBERO_BDDLDIR`, `ROBORSI_LIBERO_INITDIR` | Official LIBERO-PRO asset directories. |
| `ROBORSI_PYROKI_PORT` | Port of the PyRoKi IK / trajectory service. |
| `GRASPGEN_HOST`, `GRASPGEN_PORT` | GraspGen grasp service. Leave `GRASPGEN_PORT` empty to disable it; grasps then use the fallback planner. |
| `ROBORSI_EVAL_WORKERS` | Parallel episodes (default 8). Each worker loads its own perception models (about 5 GB of GPU memory). |
| `ROBORSI_PERCEPTION_DEVICE` | Device for the perception models (default CUDA when available); set `cpu` when GPU memory is short, or `spread` to place each episode process, both its scene rendering and its perception models, on the visible GPU (`CUDA_VISIBLE_DEVICES`) with the most free memory, so that parallel workers share the GPUs. |
| `ROBORSI_ROUND_TOOL_BUDGET` | Tool calls one round may use (default half the episode budget, at least 20), so a wrong first round leaves budget for the Reviewer's correction. |
| `ROBORSI_TOOL_BUDGET` | Episode tool budget used by `scripts/run_paper_campaign.sh` (default 80, the paper setting). |
| `ROBORSI_EPISODE_MAX_ROUNDS` | Maximum Planner–Engineer–Reviewer rounds within one episode (default 6). |
| `ROBORSI_ROLE_SESSION` | Planner and Reviewer run as persistent agent-CLI sessions, one per role and task, and the Manager as one persistent session across all tasks; all are compacted once they exceed `ROBORSI_SESSION_ROLL_CHARS` (default 600000) (`codex` for OpenAI models, `claude` otherwise; override with `ROBORSI_ROLE_BACKEND`). The same sessions are used in frozen mode. `0` uses stateless API calls. |
| `ROBORSI_GATE_SEEDS` | Consecutive development seeds (default 21,22,23) for the episode-level gate the Manager uses when a skill change has no harness of its own: on the source task the change must succeed at least once and do at least as well as the current skill on these seeds (the same comparison applies when a change carries its own harness and misses that harness's bar, unless it crashed); for global promotion it must do at least as well on other tasks that use the skill. |
| `ROBORSI_REVIEW_MODE` / `--review` | `manager` (default): skill code that passes the simulator gate is installed by the Manager for the source task only (`skills/task_local/<task>/`), since the gate validated it only there; when the Manager recommends a shared (global) change, `ROBORSI_GLOBAL_PROMOTION` decides: `manager` (default) promotes it after a cross-task gate (on `ROBORSI_GLOBAL_GATE_TASKS`, default 2, other tasks that use the skill, the change must do at least as well as the current skill on the development seeds); `human` lists it on the HTML page for a person (`apply_proposal <id> --skip-harness --scope global`). `human`: it is not published; each change is listed with its diff, the Manager's reason and the gate result at `ROBORSI_HOME/proposal_html/index.html`, to be approved with `python -m roborsi.agents.evolution.apply_proposal <id> --skip-harness` or rejected with `--reject`. |
| `ROBORSI_REPRO_ROOT` | Where the one-click script keeps environments and assets (default `~/.roborsi/repro`). |

Within an episode, the Planner plans, the Engineer executes, and the Reviewer
judges the attempt from public evidence only (plan, Engineer summary, and the
sanitized tool trace). If the Reviewer asks to continue, the Planner re-plans
from the current scene with the Reviewer's diagnosis. The scene is not reset,
all rounds share one tool budget, and `ROBORSI_EPISODE_MAX_ROUNDS` (default 6)
caps the number of rounds. In evolve mode, every episode runs in a
fresh process and its journal row is written as soon as it finishes. The
Manager (`roborsi/agents/roles/manager/cycle.py`) runs continuously in the background:
whenever an episode queues a wiki hypothesis, plan promotion or skill
proposal, it reviews it, gates code changes in the simulator, and publishes
approved ones, which every episode started afterwards uses. The simulator predicate is evaluated once, after the
last round, and is never shown to any role.

This profile is fixed to the complete 120-task short panel, seed `0`, one
attempt per task, tool budget `80`, and reasoning effort `medium`. The campaign
manifest records the exact code revision, task order, model aliases, reasoning
effort, asset paths, worker count, and retry policy. A different model alias or
reasoning effort is a new experiment, not an exact replication.

Audit a completed or in-progress campaign directly from its journal:

```bash
roborsi eval-audit \
  ~/.roborsi/evals/suites/libero-pro-matched-pass1 \
  --check-media \
  --require-complete
```

`eval-audit` independently recomputes task-level pass@K, subset and suite
breakdowns, terminal counts, infrastructure exclusion, unresolved
implementation errors, success-lock behavior, and summary parity. It writes
`audit.json` beside the original immutable manifest and append-only journal.

### Relation to the paper

This Pass 1 is not an experiment in the paper. It measures one frozen
release, one episode per LIBERO-PRO task. The paper's LIBERO-PRO result
(297/600 episodes, 102/120 tasks) runs five episodes per task while RoboRSI
improves online; use `scripts/reproduce_paper.sh libero-pro` for that
protocol (next section).

## Reproduce the paper's simulation results

```bash
scripts/reproduce_paper.sh libero       # Table 1 LIBERO:      30 tasks x 5 episodes, evolve
scripts/reproduce_paper.sh libero-pro   # Table 1 LIBERO-PRO: 120 tasks x 5 episodes, evolve
scripts/reproduce_paper.sh libero-plus  # Table 1 LIBERO-Plus: 840 instances, LIBERO library frozen
```

`libero` and `libero-pro` set up the same environment as
`reproduce_libero_pro.sh` and then run `scripts/run_paper_campaign.sh`:
seeds 0-4 for every task, every episode run (`--run-all-seeds`), evolve
mode, tool budget 80, reasoning effort `medium`, the same model for every
role. The suites are `libero_spatial`, `libero_object` and `libero_goal`
for LIBERO and the twelve perturbed short suites for LIBERO-PRO. Evolution
happens on a copy of the skill library under `<out>/library`, with the wiki
and sessions in `~/.roborsi-paper-<benchmark>`.

`libero-plus` runs `reproduce_libero_plus.sh` from the library produced by
the `libero` run, with that run's wiki, in frozen mode, as in the paper
(the library obtained on LIBERO is held fixed on the perturbed instances).

Each run ends with the audit and a line such as
`episode success 84/150 (56.0%) · tasks solved 24/30 (80.0%)`, the two
columns of Table 1. Because RoboRSI learns during the run, the numbers vary
with the model and with what a particular run learns; expect results near,
not equal to, the paper's. RoboTwin runs in its own environment
([sim-robotwin.md](./sim-robotwin.md)) and has no one-click script.

## Benchmarking

`roborsi bench skill` defaults to frozen evaluation:

```bash
roborsi bench skill click_bell.zeroshot --seeds 5
```

Use `--mode evolve` only when the benchmark is intentionally part of an
evolution campaign.

## Environment mode

For an existing CLI or service process:

```bash
export ROBORSI_RUN_MODE=frozen
```

The same runtime guards apply. Long-horizon evaluation is not exposed by the
frozen CLI yet; `roborsi eval` currently accepts atomic tasks only.

## LIBERO-Plus one-click run

```bash
export OPENAI_API_KEY=... ROBORSI_EVAL_MODEL=...
scripts/reproduce_libero_plus.sh
```

Installs the environment, checks out LIBERO-Plus (sylvestf/LIBERO-plus at
4976dc3) with its official assets, and runs the current release frozen on the
840-instance panel (`--panel libero_plus_840`: 7 perturbation types x 120,
seed 5). It ends with the journal audit and a success table per perturbation
type (`python -m roborsi.evaluation.panels breakdown <dir>`). State lives in
`~/.roborsi-libero-plus` so it does not overwrite a LIBERO-PRO setup.
