---
name: stack_bowls_demo.execute
kind: long_horizon_subskill
parent: stack_bowls_demo
phase: execute
description: Long-horizon entry point for BiCoord stack_bowls. Execution runs through the 3-role triangle (LHPlanner → LHExecutor → LHReviewer); this skill carries the task wiki + LH-intent identity.
---

# stack_bowls_demo / execute

Long-horizon execution runs through the 3-role triangle
(`LHPlanner → LHExecutor → LHReviewer`), driven by `_run_lh_3role` in
`roborsi.channels.agent.feishu.bot_agent`. This skill dir is the LH
task's published identity: `discover()` registers `stack_bowls_demo.execute`,
the task wiki (`wiki.md`) lives here, and `_detect_lh_intent` enumerates
`.execute` skill names to route LH requests. `policy.run()` is not directly
runnable — it raises `NotImplementedError`.
