# Repository Instructions

- This public repository contains the RoboRSI LIBERO short reference runtime.
- Keep unrelated experiments, credentials, machine-specific paths, and private
  service configuration outside this repository.
- Hidden simulator state and task-success predicates must never enter
  agent-visible prompts, skills, plans, or tool outputs.
- Count success only from the final post-episode simulator verdict.
- Preserve failed candidates, traces, trajectories, logs, and successful-seed
  resume protection.
- Run the documented core and runtime checks before publishing.
