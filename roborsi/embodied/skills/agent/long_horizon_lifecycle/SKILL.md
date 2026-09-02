---
name: long_horizon_lifecycle
kind: agent
domain: orchestration
version: 0.1.0
description: Long-horizon task lifecycle agent. Given a long-horizon task name, decomposes the user's instruction into atomic candidates, ensures each atomic is ACTIVE (drives atomic_lifecycle for any not-yet-trained ones), then dispatches the long-horizon execute and runs progress_judge.
metadata:
  tags: [agent, long_horizon, orchestration]
inputs:
  task_name: { type: string, required: true }
  instruction: { type: string, description: "natural-language goal" }
returns:
  state: string
  trace: list
  report: dict
---

# long_horizon_lifecycle (agent)

The long-horizon counterpart to `atomic_lifecycle`. Auto-creates missing
atomics on demand, then drives the long-horizon execute.

## State machine

```
[PARSING] → user instruction → candidate atomic list
   ↓
[CHECKING] → for each atomic, detect_state()
   ↓
   missing? → spawn `atomic new` + ask user / sub-agent for spec → SCAFFOLDED
   not ACTIVE? → drive atomic_lifecycle on it (collect+train+eval) until ACTIVE
   ↓
[READY] → all candidate atomics are ACTIVE (or zeroshot, if collection-only mode)
   ↓
[EXECUTING] → LH triangle (LHPlanner → LHExecutor → LHReviewer) runs plan→atomic*→review
   ↓
[CRYSTALLIZE] → see "Crystallizing a working recipe" below
   ↓
[POSTTRAINING] → trace ingested into _lib.rl.pi0_posttrain (skeleton)
```

## Crystallizing a working recipe

**RULE: do NOT crystallize after 1 success.** A single success may be lucky;
the recipe over-fits to one scene. Wait for ≥ N successful traces (default
N=3), then distill the common pattern.

State → action mapping the agent MUST follow:

| Per-atomic success count | Mode | Action |
|---|---|---|
| 0 | EXPLORE | Use loose instruction = "GOAL + tool list + figure it out". `agent.explore.build_explore_instruction()` builds it. Inject top-3 successful traces from this atomic if any exist (RAG history). Run more seeds. |
| 1-2 | COLLECT | Same as EXPLORE. The first success was likely lucky — collect 2 more before deciding the pattern. Do NOT touch the instruction string. |
| ≥ 3 | DISTILL | Read all successful traces (`~/.roborsi/zeroshot_traces/<atomic>/*.json`). Find the common (tool, args) sequence. Generate a prescriptive 6-12 step recipe matching the style of `pick_block_bicoord/zeroshot/policy.py`. OVERWRITE the atomic's zeroshot/policy.py instruction string. Re-run 3 more seeds to verify the crystallized recipe holds (≥ 2/3). |
| ≥ 15 (after distill) | TRAIN | Trigger `atomic spin <atomic>` → ACT/π0 finetune → eval → flips active_executor to policy:<ckpt>. Recipe is no longer used; deterministic policy takes over. |

### Where successful traces live

`~/.roborsi/zeroshot_traces/<atomic>/<run_id>.json` — one file per
vlm_declared_success run. Schema: `{task_name, seed, instruction, expected,
trace: [(tool_call, result), ...], outcome, sim_check_success}`.

### Distill heuristic (for the agent's reading)

1. Load all traces for `<atomic>`.
2. Extract `(tool, args.keys())` ordered tuple from each.
3. Compute the longest common subsequence across traces (or just the most
   frequent prefix/sequence).
4. Generalize args: keep tool names + arg names; for each arg, look at value
   distribution across traces and either fix it (low-variance) or templatize
   (high-variance — reference upstream tool result, e.g. `top.grasp_pose[2]`).
5. Add a `MANDATORY workflow` block + FORBIDDEN entries listing the failure
   modes from the failed (non-persisted) attempts you remember.

This is **the agent's responsibility**, not Tier 1's. Tier 1 changes the
framework (base skills, runtime, scaffold templates, lifecycle code). Recipe
content + when to crystallize is owned by Tier 2.

## Authoring a new base skill (when stuck)

If existing base skills can't solve a phase and recipe tweaks aren't
helping, **you (Tier 2) may author a new base skill**. Tier 1 normally owns
framework code, but tool gaps surface during execution and you have the
context to identify them.

Decide tool vs recipe:
- "I need to filter pointcloud by Z" → tool (mechanical capability)
- "I need to retry grasp at varying heights" → recipe (composition of existing tools)
- "I need force-closure detection on the gripper" → tool
- "Drop block from 30cm not 5cm" → recipe (just call gripper(open) higher)

Steps to add a new tool (NO rollout_runtime.py edit required):
1. `roborsi-sim base new <tool_name> --category <perception|control|geometry|policy|active_perception> --description "<one-line>"`
   → scaffolds `skills/base/robotwin/<tool_name>/{SKILL.md, policy.py}`
2. In the new `policy.py`, implement:
   ```python
   def dispatch_runtime(state, args):
       """Called by rollout_runtime._dispatch when the VLM calls this tool.
       state.env is the live RoboTwinEnv; state.workdir is the per-episode dir.
       Return (result_dict, SimObservation).
       """
       from roborsi.embodied.sim.robotwin.rollout_runtime import _snapshot, _write_jpg
       # ... use args, state.env._impl, etc. ...
       return ({"ok": True, ...}, _snapshot(state.env))
   ```
3. Update SKILL.md `args:` schema — the auto-prompt + tool spec picks it up.
4. Test by calling the tool from a one-off zeroshot atomic. The `_dispatch`
   in rollout_runtime auto-discovers `dispatch_runtime` via the plugin path
   — you DO NOT need to edit `_dispatch` or add a `_do_<name>` function.
5. (Optional) For complex tools needing access to private helpers
   (`_call_vlm_image`, `_unproject`, etc.), import them from
   `roborsi.embodied.sim.robotwin.rollout_runtime`.

This makes you self-sufficient when the toolset is the bottleneck. Document
WHY you added the tool in the SKILL.md description so Tier 1 can later
promote / refactor.

Skeleton for v1 — drives existing long-horizon tasks. Auto-creating atomics
from a brief is the next iteration.
