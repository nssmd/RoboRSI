# Vendored: _codex_autoloop

Vendored copy of the **ArgusBot** `codex_autoloop` module (drives the
`codex` / `claude` / `copilot` CLI as a persistent-session subprocess).

- Upstream: <https://github.com/waltstephen/ArgusBot>
- License: MIT
- Files: `__init__.py`, `codex_runner.py`, `models.py`, `runner_backend.py`
  (self-contained — only stdlib + relative imports; `RunnerOptions` lives in
  `codex_runner.py`).

## Why vendored

RoboRSI uses `CodexRunner.run_exec(prompt, resume_thread_id, options)` to run
the **Planner** and **Reviewer** as persistent `claude` sessions
(`roborsi/agents/persistent_agent.py`) instead of stateless API calls, so
those roles accumulate cross-run memory. Vendoring avoids a hard dependency on
the separate `argus-skill` package.

Refresh this vendored directory by reviewing the upstream diff and running the
persistent-session tests.
