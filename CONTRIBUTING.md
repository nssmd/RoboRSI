# Contributing

## Development Setup

```bash
./setup.sh --core-only --dev
source .venv/bin/activate
pytest -q -m "not runtime"
```

The core environment covers configuration, evidence replay, the Web console,
release hygiene, and package construction. Runtime tests are marked
`runtime`; install the complete environment with `./setup.sh --dev` before
running `pytest -q`.

## Required Invariants

- Keep simulator success post-episode and host-only.
- Do not expose reward, predicate source, object poses, private simulator
  state, or a completion latch to any role, prompt, memory, or skill.
- Count only final simulator verdicts; retain and exclude infrastructure rows.
- Preserve every journal, trace, trajectory, video, proposal, and failed run.
- Never rerun an already successful task/seed pair.
- Keep adaptive and fixed result schemas separate.
- Store credentials only in environment variables.

## Skill Changes

A visible skill change needs:

1. a failing focused test;
2. a complete `policy.py`, not a patch fragment;
3. static hidden-input checks;
4. a retained simulator harness run;
5. native simulator success before promotion.

Do not hard-code demonstration coordinates or tune against hidden task state.

## Pull Requests

Keep changes scoped. Include the command and fresh output that verify the
change, note any simulator/API test that could not run, and avoid committing
generated runs or credentials. Before review, run:

```bash
pytest -q -m "not runtime"
ruff check src/roborsi_libero tests scripts
python scripts/release_check.py
./reproduce.sh --skip-setup --output-dir /tmp/roborsi-reproduction
python -m build
```

Changes to simulator execution, skills, workers, or promotion logic also require
the complete runtime environment, a full `pytest -q`, and:

```bash
python scripts/check_libero_gt_leak.py
```
