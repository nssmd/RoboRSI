---
name: match_blocks_bicoord.execute
kind: long_horizon_subskill
parent: match_blocks_bicoord
phase: execute
description: Long-horizon entry point for BiCoord match_blocks_with_signs. Execution runs through the 3-role triangle (LHPlanner → LHExecutor → LHReviewer); this skill carries the task wiki + LH-intent identity.
---

# match_blocks_bicoord / execute

Long-horizon execution runs through the 3-role triangle
(`LHPlanner → LHExecutor → LHReviewer`), driven by `_run_lh_3role` in
`roborsi.channels.agent.feishu.bot_agent`. This skill dir is the LH
task's published identity: `discover()` registers `match_blocks_bicoord.execute`,
the task wiki (`wiki.md`) lives here, and `_detect_lh_intent` enumerates
`.execute` skill names to route LH requests. `policy.run()` is not directly
runnable — it raises `NotImplementedError`.
