# Frozen Evaluation

RoboRSI has two explicit runtime modes:

- `evolve`: normal operation; validated experience may update persistent
  capability.
- `eval`: frozen operation; the current release executes without changing what
  later runs can do.

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
`~/.roborsi/evals/` while eval mode is active.

## Run LIBERO short task-level pass@K

```bash
roborsi eval-suite \
  --backend libero-pro \
  --pass-at 5 \
  --seed-start 0 \
  --workers 4 \
  --tool-budget 40 \
  --code-on \
  --out ~/.roborsi/evals/libero-pro-pass5
```

`--code-on` is the default and exposes released, code-backed compound skills
without allowing any capability write-back. Use `--code-off` only for the
matched ablation.

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

After configuring the official LIBERO-PRO BDDL and init-state directories and
starting PyRoKi, run:

```bash
ROBORSI_EVAL_MODEL=gpt-5.6-sol \
ROBORSI_EVAL_WORKERS=8 \
scripts/run_libero_pro_matched_pass1.sh \
  ~/.roborsi/evals/suites/libero-pro-matched-pass1
```

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

### Historical result boundary

The previously reported LIBERO-PRO `80/120` is a historical adaptive,
cross-release seed-0 result. It accumulated successes across five code
releases and reran only tasks that had not yet succeeded. Its first-stage
`43/120` was itself a closure over several disjoint runs and recovery runs.
Neither number is a fixed-policy single pass.

The script above intentionally measures a stricter object: one current frozen
release over all 120 tasks. It is the supported path for a fresh simulator
rerun. Historical summary artifacts can be audited as retained evidence, but
they must not be presented as though this command deterministically recreates
the old `80/120`.

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
export ROBORSI_RUN_MODE=eval
```

The same runtime guards apply. Long-horizon evaluation is not exposed by the
frozen CLI yet; `roborsi eval` currently accepts atomic tasks only.
