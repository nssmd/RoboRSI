# RoboRSI Agents

The self-evolution **government** that turns a Feishu / terminal request into a
done robot task and a better skill library. One persistent **Manager** oversees
the per-task triangle:

```
user (Feishu / `roborsi chat`)
        │
        ▼
   Manager (persistent Claude session, agents/manager/MANAGER.md)
        │  dispatches + approves
        ▼
   Planner ─► Engineer ─► Reviewer ─►  proposal ─► harness gate ─► apply
   plan.md     run sim      verdict          (validator.py)
        └──────────── task_wiki / plan.md (durable memory) ─────────┘
```

## Roles (each = one file here)

| Role | File | Does |
|---|---|---|
| **Manager** | `manager/MANAGER.md` | Persistent session; reads user, dispatches the triangle, reviews + applies gated proposals. The only role that APPLIES. |
| **Planner** | `planner.py` | Opus call → writes `plan.md` before execution. |
| **Engineer** | `engineer.py` | Opus drives the sim tool loop → runs the task, writes `summary.md`. |
| **Reviewer** | `reviewer.py` | Reads plan/summary/trace → verdict + skill proposal. |
| **LH Planner / Executor** | `lh_planner.py` / `lh_executor.py` | Long-horizon decompose + sustained multi-agent execution. |
| Self-evo support | `skill_synthesizer.py`, `env_synthesizer.py`, `validator.py` (gate), `task_wiki.py`, `skill_selector.py`, `skill_history.py`, `plan_archive.py`, `atomic_bottleneck.py` | Synthesize skills/envs, gate proposals, accumulate per-task knowledge. |
| Runtime | `persistent_agent.py`, `_codex_autoloop/`, `workspace.py`, `html_review.py` | Persistent-session engine + task workspace + proposal review surface. |

## Conventions every agent follows

- **No sim-cheating.** Never `use_attach` / teleport / physics override. Success
  comes from the final post-episode simulator verdict, never a VLM
  `done(success=True)` self-report.
- **No hardcoded magic numbers / copied expert poses** — calibrate from sim, then
  bake the constant into `policy.py` with a comment citing how it was derived.
- **Debug scripts live WITH their skill, and die when done.** A throwaway probe /
  calibration / diagnostic script an agent writes goes in that skill's own
  folder — `embodied/skills/<tier>/<name>/scripts/` — **never** in the repo-root
  `scripts/`. The root `scripts/` is for durable entrypoints only (campaign
  loops, CLIs, self-evo drivers, renders). When the investigation is finished:
  bake the result into the skill (`policy.py` constant / `plan.md` step), cite it
  in a one-line comment, and **delete the script**. Leftover debug scripts are
  clutter — the knowledge belongs in the skill, not in a pile of one-off files.
- **Harness gate on every apply.** Skill changes pass `validator.py`; the Manager
  reviews + applies (escalates to the user only when genuinely unsure).
- Respect the operator's configured compute allocation and Git identity.

See `manager/MANAGER.md` for the Manager's full brief and `PROCESS_PERMISSIONS.md`
for the apply-permission model.
