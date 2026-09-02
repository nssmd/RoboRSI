---
name: roborsi-self-evolution-loop
description: |
  Build a self-evolving long-horizon agent system that runs a robot sim
  task, diagnoses its own failures across 3 layers (gate / agent / base
  skill), proposes fixes via review queue, and re-runs after a human (or
  another AI) approves. Includes a per-base-skill harness validator,
  an institutional-memory file (EDIT.md), and per-run gate-log artifacts
  so subtle silent-False bugs surface within one iteration instead of
  burning a 3-hour orchestrator round.

  Use when: building an agent that has to debug its OWN robotics
  framework, not just call it; needing a tight diagnose→propose→review→
  apply loop with human-in-the-loop checkpoints; or shipping infra where
  silent gate / verify bugs cascade if you only watch top-line success rate.
metadata:
  type: methodology
  scope: roborsi-self-evolution
  tested_at: 2026-06-04
---

# RoboRSI Self-Evolution Loop — Pattern

## What this builds

An agent that runs `<task>.execute` (a long-horizon plan of 2-4 atomics
in a BiCoord-Bench sim), and when the run fails it does its OWN
diagnostic chain — read `get_lh_report`, dump `get_inner_trace` from
sqlite, `view_frame` on pre/post jpgs, `read_file` on the BiCoord env
source — then submits a `propose_skill_update` / `propose_new_skill` to
`~/.roborsi/skill_review/`. A reviewer (human or another LLM) applies
or rejects via `scripts/apply_selfevo_proposal.py`; a harness gate
(`scripts/test_base_skill.py`) blocks any base-skill change that doesn't
pass its own SKILL.md `harness:` block before commit. The next run uses
the new code.

The loop has four moving parts (described below) and one institutional
memory file (`EDIT.md`) that captures every class of failure mode the
loop has produced, so future iterations stop reproducing the same
mistakes.

## The 4 moving parts

```
PART                         WHAT IT IS                            WHERE
agent diagnostic surface     22 tools the outer agent can call:    bot_agent.py
                             read_file/list_dir/grep_repo,
                             get_inner_trace, view_frame,
                             gate_log inspection, run_python.

structural gates             Per-atomic post-episode override:     atomic/<name>/zeroshot/policy.py
                             "VLM said done but trace shows
                             never_called_<X>" → outcome=
                             never_attempted_grasp. Every fire
                             logs to ~/.roborsi/gate_log.jsonl.

self-evo proposal queue      Agent submits {kind, name, code,      ~/.roborsi/skill_review/
                             rationale}. Reviewer enforces 7
                             rules (see review_selfevo_proposal
                             SKILL.md). For base/robotwin/*
                             changes, harness gate runs first.

base-skill harness           Each base skill carries `harness:`    test_base_skill.py
                             in its SKILL.md frontmatter (args +
                             pass_criteria). Batch runner reports
                             PASS/FAIL/SKIP/MALFORMED with diff
                             from the previous batch.
```

## The agent's diagnostic order (codified as a SKILL)

`_lib/human_review/diagnose_atomic_failure/SKILL.md` lays out 6 sections
A-F that the agent walks top-to-bottom, stopping at the first match:

| Section | Trigger | Tool calls | Decision |
|---|---|---|---|
| A | judge vs outcome agree | get_lh_report | continue to C |
| B | gate says fail, judge says success | get_inner_trace, read_file gate code | propose gate fix |
| C | VLM didn't follow recipe | inner_trace tool histogram | propose prompt update |
| D | motion-planning IK fails | get_sim_debug | propose param tweak |
| E | motion completes but verify=False | read BiCoord env source, model_data*.json | propose new wrapper base skill |
| F | post-fix verification | git_log | confirm + move on |

E is the most valuable: that's where the agent reads
`$ROBORSI_BICOORD_ROOT/envs/<task>.py:play_once`,
finds the expert's primitive (e.g. `grasp_actor(cup, contact_point_id=2)`),
and proposes wrapping it.

## Two key contracts

### 1. SKILL.md harness frontmatter — the test spec

```yaml
---
name: pick_actor_by_contact_point
kind: base_skill
category: base/robotwin
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [1, 2, 3, 11, 17]
    args:
      - {arm: right, actor_name: cup_2, contact_point_id: 0}
    pass_criteria:
      kind: grasp_holds_actor
      min_seeds_passing: 3
---
```

`scripts/test_base_skill.py --batch` iterates every `base/robotwin/*`
SKILL.md, runs the listed args at every seed, grades by `pass_criteria`,
and writes a JSON report to `~/.roborsi/harness_reports/<ts>.json`.
Any apply-script path (CLI or Feishu `/approve`) gates on this — no
unverified base skill enters main.

### 2. `gate_log.jsonl` — the structural-gate audit trail

Every structural-gate override logs one JSON line:

```json
{"ts":"2026-06-04T13:55","kind":"fire","atomic":"pick_bowl_bicoord",
 "gate_outcome":"unverified_grasp_right",
 "primitives_seen":["describe_scene_actors","pick_actor_by_contact_point","done"],
 "trace_steps":3}
{"ts":"2026-06-04T14:01","kind":"contradiction","atomic":"pick_bowl_bicoord",
 "gate_outcome":"unverified_grasp_right","judge_success":true,
 "judge_reason":"Right gripper holds the bowl lifted above the table"}
```

`kind=contradiction` (gate=fail + judge=True) is the canonical
"broken gate" signal — caught the V8 set-str bug, the V10 wrapper-internal-
verify miss, and the V11 sim-predicate misuse, each within one round.

## The base-skill validation pipeline

```bash
# Single skill
python scripts/test_base_skill.py <skill> --from-frontmatter
# Batch — 56 skills, ~30 min on a B200 with cached models
python scripts/test_base_skill.py --batch
```

```
scripts_lib_harness_gate.py — single source of truth for "what counts
                              as PASS". Imported by both
                              apply_selfevo_proposal.py and the Feishu
                              /approve handler so the gate is identical.
```

A failing harness halts apply with exit code 3 and reverts the
working tree — operators can `--skip-harness` only with explicit intent.

## The institutional-memory contract (EDIT.md)

A flat numbered list of failure modes the system has produced, each
with a `**Worked failure**` block citing the actual run/commit. 12
entries today (set-str char-split, wrapper-internal-verify, sim
ground-truth misuse, etc.). Rule: re-read top-to-bottom before any
edit; add a new section every time a class of mistake recurs.

The point of EDIT.md is not documentation — it's a *check* that runs
before code. If a planned change conflicts with anything in it, stop
and redesign.

## Gotchas (learned the hard way, each in EDIT.md with date + repro)

1. **`set("string")` returns a character set.** `trace_invoked(trace,
   "tool_name")` was silently False-for-every-input for two weeks
   because `set("tool_name")` is `{'t','o','l','_',...}`. Always
   `if isinstance(x, str): x = {x}` before constructing a set.

2. **`stream.get_final_message().content` is a list of TextBlock
   *objects* (not dicts).** `c.get("text")` returns None on these →
   reply text gets silently dropped. Need an `_extract_text_block()`
   that handles dict / str / object shape.

3. **Anthropic `max_tokens=32000` triggers a streaming-required
   threshold.** SDK rejects non-streaming call with 400. Either drop
   to ≤16k OR switch to `client.messages.stream()` + `.get_final_message()`.

4. **Sim's `check_success()` encodes the FULL task predicate.** Calling
   it after EVERY atomic sub-episode (in rollout_runtime's overclaim
   check) marks every atomic `vlm_overclaimed` because the full
   predicate is provably False mid-handover. Atomics must opt out
   (`use_sim_predicate=False`).

5. **Empty assistant text + no tool_calls (LLM going silent) is a
   real Anthropic state.** Prefill (`{"role":"assistant","content":
   [{"type":"text","text":"Format "}]}`) + `tool_choice={"type":"none"}`
   physically forces non-empty continuation.

6. **Apply script must move applied/rejected files OUT of the queue
   root.** Otherwise `list_dir(skill_review/)` shows stale 'pending'
   files; agent stops proposing thinking the queue is full.

## File map

```
roborsi/
├── channels/agent/feishu/
│   ├── bot_agent.py            # outer agent: system prompt + 22 tools + force-synth loop
│   ├── feishu_review.py        # /harness, /approve, /reject slash commands
│   └── feishu_integration.py   # bot_server entry → handle_command dispatch
├── embodied/
│   ├── sim/robotwin/
│   │   └── rollout_runtime.py  # inner sim VLM tool loop, _do_<X> handlers, _check_success
│   └── skills/
│       ├── _lib/
│       │   ├── evaluation/
│       │   │   ├── trace_inspect.py    # gate primitives (with unit tests in tests/)
│       │   │   └── gate_logger.py      # ~/.roborsi/gate_log.jsonl writer
│       │   ├── human_review/
│       │   │   ├── harness_standard/SKILL.md          # frontmatter schema + 5 pass-criteria kinds
│       │   │   ├── review_base_skill_harness/SKILL.md # harness-gate procedure
│       │   │   ├── review_selfevo_proposal/SKILL.md   # 7-rule decision tree
│       │   │   ├── diagnose_atomic_failure/SKILL.md   # A-F checklist
│       │   │   └── expert_trajectory_reflection/SKILL.md
│       │   └── orchestrate/long_horizon_executor/policy.py
│       ├── base/robotwin/                 # 56 base skills, each with harness: block
│       │   ├── pick_actor_by_contact_point/
│       │   ├── describe_scene_actors/
│       │   └── ...
│       ├── atomic/                        # composed VLM-loop skills with structural gates
│       └── long_horizon/                  # 4-step plans
├── scripts/
│   ├── apply_selfevo_proposal.py  # harness-gated apply + git commit + archive
│   ├── test_base_skill.py      # batch + --from-frontmatter modes
│   ├── scripts_lib_harness_gate.py  # shared GateResult; called by both apply paths
│   ├── feishu_notify_review.py # push pending cards to Feishu (opt-in via env)
│   └── diag_silence.py         # repro the LLM-goes-silent bug in isolation
├── tests/_lib/evaluation/
│   └── test_trace_inspect.py   # 18 pytest cases, two regression-named to the bug they cover
└── EDIT.md                     # 12-entry pre-edit checklist (institutional memory)
```

## When to reuse this pattern

- Long-horizon agent systems where success rate ≤ 50% and you need
  fast diagnosis before each iteration burns 1-3 h of GPU.
- Robotics frameworks with three layers (motion primitives / atomics /
  plans) where bugs at one layer mask as failures at another.
- Any agent loop that proposes code changes — the harness gate +
  EDIT.md prevent the "agent that fixed the symptom and broke the
  invariant" failure mode.

## When NOT to reuse it

- Single-shot agent demos with no learning loop — too much scaffolding
  for one run.
- Systems where the agent isn't authoring code (pure tool-use chat
  bots) — propose / harness / EDIT.md are dead weight.
- Open-domain LLM evals — the per-skill harness assumes a
  deterministic-enough sim env.
