# Repository Instructions

- This public repository contains the RoboRSI LIBERO short reference runtime.
- Keep website assets, private endpoints, credentials, operator paths, OPD,
  and long-horizon work outside this repository.
- Hidden simulator state and task-success predicates must never enter
  agent-visible prompts, skills, plans, or tool outputs.
- Count success only from the final post-episode simulator verdict.
- Preserve failed candidates, traces, trajectories, logs, and successful-seed
  resume protection.
- Run `pytest -q`, scoped Ruff, `scripts/release_check.py`, evidence replay, and
  package build before publishing.
