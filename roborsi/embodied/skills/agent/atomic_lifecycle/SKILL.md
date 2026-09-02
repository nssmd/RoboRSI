---
name: atomic_lifecycle
kind: agent
domain: orchestration
version: 0.1.0
description: End-to-end atomic-skill lifecycle orchestrator. Given an atomic task name, drives scaffold → zeroshot collection → train → eval → active_executor switch in a closed loop, hands-off. The data flywheel.
metadata:
  tags: [agent, orchestration, flywheel]
inputs:
  task_name:      { type: string, required: true, description: "atomic task name, e.g. pick_block_bicoord" }
  sim_task:       { type: string, required: true, description: "underlying BiCoord/RoboTwin task (env.make_env)" }
  backend:        { type: string, default: bicoord }
  spec:           { type: string, description: "natural-language description of the task — only used at scaffold time" }
  seeds:          { type: list, default: [1, 2, 3, 4, 5] }
  success_target: { type: float, default: 0.5 }
  episode_target: { type: int, default: 25, description: "minimum success episodes before triggering train" }
returns:
  state: "string (final state machine state)"
  active_executor: "string (zeroshot|policy:<ckpt>)"
  report: "summary of phases run"
---

# atomic_lifecycle (agent)

The orchestrator agent for one atomic task. Reads this skill, then drives
the lifecycle. Implements the data flywheel from `roborsi-story.md`.

## State machine

```
[ABSENT] ─ scaffold ─→ [SCAFFOLDED]
            ↓
        [COLLECTING] ── zeroshot N seeds ──→ DataStore
            ↓
        (count successes ≥ episode_target?)
            ↓ yes
        [READY_TO_TRAIN] ── train ──→ checkpoint
            ↓
        [TRAINED] ── eval ──→ eval_report.json
            ↓
        (success_rate ≥ success_target?)
            ↓ yes                        no ↓
        [ACTIVE] (active_executor=policy:ckpt)   →  back to COLLECTING
```

States are derived from filesystem state — no separate state file:

| State | Detected by |
|---|---|
| ABSENT | `skills/atomic/<task>/` does not exist |
| SCAFFOLDED | dir + 4 sub-skill dirs (zeroshot/train/eval/reset_*) exist with policy.py |
| COLLECTING | < `episode_target` successful episodes in DataStore for `<task>` |
| READY_TO_TRAIN | ≥ `episode_target` successes, no checkpoint yet |
| TRAINED | checkpoint dir under `~/.roborsi/checkpoints/local/<task>*` |
| EVALED | `eval_report.json` under `~/.roborsi/evals/<task>/<ts>/` |
| ACTIVE | `~/.roborsi/atomic_state/<task>/active_executor.json` says `policy:<ckpt>` |

## Phases

### 1. SCAFFOLD
Create `skills/atomic/<task>/{zeroshot, train, eval, reset_success, reset_failure, judge}/{SKILL.md, policy.py}`.

Templates inject:
- task_name into `_SKILL_LABEL` constants
- spec into the zeroshot `instruction` (VLM gets task description)
- sim_task + backend into reset/eval (so they spawn the right env)
- judge criterion derived from spec ("the gripper holds X lifted clear of Y")

Use `roborsi atomic new <task>` (CLI wraps this).

### 2. COLLECT
Loop:
- For each seed in `seeds` (or until `episode_target` successes):
  - `roborsi long-horizon run <wrapper>_<task> execute --params '{"seed": s}'`
    OR atomic-only `roborsi atomic run <task> zeroshot --params '{"seed": s, "spawn_task": "<sim_task>"}'`
  - Each run captures into `~/.roborsi/data/<task>/<run_id>/`
  - Successes labelled by atomic.judge subskill (Claude subprocess VLM judge)
  - Track count in `~/.roborsi/atomic_state/<task>/zeroshot_count.json`

Stop conditions:
- `episode_target` successes → READY_TO_TRAIN
- Exhausted `seeds` budget without enough successes → human intervention
  (poll user: "X% success rate so far, want to widen seed range or fix recipe?")

### 3. TRAIN
- `roborsi atomic run <task> train --params '{"base_model": "act", "steps": 2000, "dataset_name": "<task>_v1"}'`
- Wait for ckpt path in result. Save under `~/.roborsi/checkpoints/local/<task>_v1/<run>/checkpoints/<step>/pretrained_model`.

### 4. EVAL
- `roborsi atomic run <task> eval --params '{"executor": "pi0_checkpoint", "seeds": 5, "seed_start": 1000, "threshold": <success_target>}'`
- Eval reads latest ckpt. Writes `eval_report.json`. If success_rate ≥ threshold, the eval skill itself flips `active_executor.json` to `policy:<ckpt>`.

### 5. SWITCH (automatic via eval)
- After eval, read `active_executor.json`. If `executor.startswith("policy:")` → ACTIVE.
- ACTIVE state means future zeroshot calls (e.g. inside long-horizon) auto-route to the trained policy via the executor-resolution in the LH triangle (LHExecutor).

### 6. KEEP SPINNING (data flywheel)
Once ACTIVE, the policy can be used to generate MORE successful trajectories (which judge says success). Those go back into DataStore. Trigger retrain after another ΔN successes. The flywheel.

## How to use this skill

The Claude Code agent (you) reads this SKILL.md, then issues these calls:

```python
from roborsi.agent.atomic_lifecycle import drive
drive(task_name="pick_block_bicoord",
      sim_task="handover_block_with_bowls",
      backend="bicoord",
      seeds=range(1, 26),
      success_target=0.4,
      episode_target=15)
```

Or via CLI: `roborsi-sim atomic spin pick_block_bicoord --params '{...}'`

## Failure modes

| State stuck at | Likely cause | Action |
|---|---|---|
| COLLECTING (low success rate) | VLM tool recipe wrong / sim too hard | Iterate the atomic.zeroshot instruction; try different seeds; consult sub-agent for image debugging |
| TRAINED but eval rate low | Not enough data / wrong base model | Bump `episode_target`, retrain with more steps, or relax `success_target` |
| EVAL crashes | Sim env spawn failed | Check `ROBORSI_ROBOTWIN_ROOT` env var |

## Notes

- This agent is **deterministic**: each phase reads/writes filesystem state, then the next phase is decided by re-detecting state.
- An LLM-driven version would add a "react" step between phases (look at last result, decide whether to retry / change params / escalate).
- For now we use deterministic state machine; LLM judgement comes through `atomic.judge` (per-trajectory) and is preserved in DataStore meta.
