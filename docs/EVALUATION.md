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
infrastructure errors are reported separately as `infra_count`.

## What eval records

- the per-run `plan.md`, `summary.md`, and `review.md`;
- tool calls and visible execution trace;
- timing and final outcome in `trace.db`;
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
