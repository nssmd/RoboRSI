---
name: diagnose_atomic_failure
kind: meta_skill
description: Exhaustive ordered checklist for diagnosing why an atomic failed in a long-horizon run. Codifies the procedure I (Claude) actually follow when reviewing a failed orchestrator round, so any AI reviewer can do the same investigation. Each step says WHAT to check, WHICH tool to call, HOW to read the result, and WHEN to escalate to the next step. Stops as soon as a step finds the root cause.
version: 1
metadata:
  tags: [diagnosis, checklist, review, self-evolution, meta]
  use_when:
    - reviewing a failed bench-lh / triangle round
    - deciding whether the atomic needs prompt-only fix, base-skill fix, or new base skill
    - writing a proposal rationale
---

# Why this exists

When a long-horizon atomic fails, there are at least 6 distinct failure modes and 4 distinct fix levels (prompt / SKILL.md hint / structural gate / new base skill). Pattern-matching on judge reasons alone gets the wrong fix level (e.g., tightening the prompt when the bug is in the base skill's grasp pose computation). This checklist is the procedure that funnels you to the right fix level.

# Inputs you need

- `<atomic>` — name of the failed atomic (e.g. `pick_bowl_bicoord`).
- `<lh_task>` — the long-horizon task that calls it (e.g. `handover_block_bicoord`).
- `<sim_task>` — the BiCoord-Bench env class (e.g. `handover_block_with_bowls`). Find via `get_lh_report(<lh_task>).sim_task`.
- `<run_id>` — the latest failed run's id (from `recent_runs()` or `get_lh_report` atomics).

# Checklist (run top-to-bottom; STOP at first match)

---

## A. Is it even a real failure?

**A.1** Call `get_lh_report(<lh_task>, latest=True)`.

Read `trace[*].atomic_judge.reason` AND `trace[*].atomic_result.outcome` AND `trace[*].atomic_judge.success`.

| If you see | Then |
|---|---|
| `atomic_judge.success=true` but `atomic_success=false` | **CONTRADICTION** — judge says it worked, gate says no. Skip to section **B**. |
| `atomic_judge.reason` describes the goal state achieved (e.g. "block separated from bowl, pinched in fingertips") but `outcome` is a fail enum | Same — section **B**. |
| `atomic_judge.success=false` AND reason describes the real failed scene state (e.g. "right gripper not holding bowl, bowl on table") | Real failure. Continue to **C**. |

---

## B. CONTRADICTION SIGNAL — gate / judge / outcome disagree

**B.1** Call `get_inner_trace(<run_id>, tool_filter=<the primitive named in the recipe>)`. See what the VLM actually called.

**B.2** If the recipe's canonical primitive WAS called with `ok=True`:
- The gate (in `_lib/evaluation/trace_inspect.py`) or the .zeroshot's structural override is wrong.
- `read_file('roborsi/embodied/skills/_lib/evaluation/trace_inspect.py')` AND `read_skill_code('<atomic>.zeroshot')`.
- Trace whether the gate matches the actual trace shape and tool names.
- Fix: `propose_skill_update` on the atomic that consumes the gate, OR on `trace_inspect.py` if the helper itself is broken.

**B.3** If the judge's "success=true" reading conflicts with sim's `check_success`:
- Judge is too lenient. Look at `read_file('$ROBORSI_BICOORD_ROOT/envs/<sim_task>.py')` — find `check_success` for the real predicate.
- Fix: `propose_skill_update` on `<atomic>.judge` to tighten the prompt.

STOP here if B matched.

---

## C. Did the VLM follow the recipe?

**C.1** `get_inner_trace(<run_id>)` (no filter) — list ALL tool calls.

**C.2** Compare to the recipe in `read_skill_code('<atomic>.zeroshot')`:

| Symptom | Diagnosis | Fix level |
|---|---|---|
| Recipe says "Step 1 = find_object_via_wrist", but VLM called `find_pixel` directly | VLM ignoring recipe | Prompt strengthening (use MANDATORY / PROHIBITED clauses) |
| VLM never called the primary grasp primitive | VLM gave up early | Add HARD RULE: "PROHIBITED done(True) without ≥1 call to <primitive>". Also add a structural gate via `_trace_invoked` |
| VLM called `done(success=True)` very early in budget | Overclaim | Tighten done gate to require `verify_holding_visual=True+conf>=0.7` |
| VLM called recipe correctly, all `ok=True success=True holding_visual=True` per primitive, but atomic_result is still fail | Continue to **D** |
| VLM called recipe correctly but primitives returned `ok=True success=False holding_visual=False` | **Base skill is the problem.** Skip to **E** |
| Primitives returned `ok=False reason="<...>"` | Motion / IK problem. Skip to **D** |

---

## D. Motion-planning / IK level failures

**D.1** Inspect the physical-layer error messages. `get_sim_debug()` filters DBG / IK-fail / GraspGen lines.

**D.2** Or via sqlite directly (most reliable):
```
run_python(code='''
import sqlite3
db = sqlite3.connect("~/.roborsi/trace.db")
rows = db.execute("""SELECT tool, substr(result_preview,1,140)
                      FROM steps WHERE run_id=? AND result_ok=0
                      AND result_preview IS NOT NULL""",
                    ("<run_id>",)).fetchall()
for r in rows: print(r)
''')
```

**D.3** Interpret:

| Pattern | Meaning | Fix |
|---|---|---|
| `"no horizontal approach had IK"` repeated | cuRobo refuses any cardinal lateral. Workspace is blocked OR rim_z is wrong. | propose_skill_update the base skill to add diagonal angles, or extend descend_ladder upward |
| `"GraspGen returned 0 candidates"` | Perception/segmentation failed | Tighten the object query / try `extra_queries=[...]` / check that the object class is in GraspGen's training data |
| `"GraspGen returned N candidates, all rejected"` | Candidates exist but downstream IK rejected each. Likely sibling arm in workspace. | Add arm-parking step OR propose new base skill with extended approach angles |
| `"cuRobo plan status = Fail"` on advance phase only | Pre-grasp pose reachable, advance not. Joint limits hit at advance. | Diagonal approaches; or use contact_point_id-based grasp (D goes to E) |
| `"descend_ok=false"` | Final descent IK fails. rim_z is too low (lateral hits bowl floor) OR too high (fingers above rim). | Extend descend_ladder; or recompute rim_z |
| `IK-ok` everywhere but `holding_visual=false` | Motion completes, force-closure fails. Skip to **E** — this is base-skill geometry, not IK. |

STOP if you write the fix proposal.

---

## E. EXPERT-TRAJECTORY PROTOCOL — base skill is wrong

Triggered when EITHER:
- Section C said `ok=True success=False holding_visual=False`
- Section D said "IK-ok but no grasp"
- This is the 2+ round case where prompt tweaks aren't helping.

**E.1** Read the sim env source:
```
read_file('$ROBORSI_BICOORD_ROOT/envs/<sim_task>.py')
```

Look for the expert trajectory (usually `play_once` or named like `play_*`):
- What primitives does it use? `grasp_actor(<actor>, contact_point_id=N)`? `move_to_pose(target)`?
- What are the object/actor attribute names (`self.cup_2`)?
- Read `check_success` — the actual success predicate.

**E.2** For each actor the expert grasps via `contact_point_id`, read the asset metadata:
```
read_file('$ROBORSI_BICOORD_ROOT/assets/objects/<modelname>/model_data<N>.json')
```
Check `contact_points_pose` — the 4×4 transforms specifying tuned grasp poses.

**E.3** Survey existing base skills:
```
list_dir('roborsi/embodied/skills/base/robotwin')
grep_repo('get_grasp_pose|contact_points_pose|grasp_actor', '*.py', 'repo')
```

If a base skill already wraps the expert's primitive: the atomic's recipe is using the wrong base skill. Fix is `propose_skill_update` on `<atomic>.zeroshot` to promote the right one to Step 1.

If NO base skill wraps the expert's path: `propose_new_skill` a wrapper. Template: `roborsi/embodied/skills/base/robotwin/pick_actor_by_contact_point/policy.py`. Key constraints:

- `dispatch_runtime(state, args)` signature; accessed via `rollout_runtime._dispatch`.
- Look up actor via `getattr(env._impl, name)` — NOT `scene.get_all_actors()` (scene names collide for repeated models).
- Wrap the expert's primitive (e.g. `impl.get_grasp_pose(actor, ArmTag(arm), contact_point_id, pre_dis)`), DON'T reinvent the geometry.
- Return `holding_visual` from a real `_do_verify_holding_visual` call — no heuristics.

Also: NEVER hardcode actor names in the atomic prompt. Use `describe_scene_actors` (introspection base skill) for runtime discovery.

**E.4** Write the proposal rationale citing all of:
- `get_failure_patterns(<atomic>) → SYSTEMATIC` count
- `get_inner_trace(<run_id>) → primitive called N times, all ok=True success=False`
- expert trajectory line numbers in BiCoord env source
- asset model_data contact_points_pose annotation
- list of existing base skills surveyed, why none cover this

Without these citations the proposal will be REJECTed per `review_selfevo_proposal` Rule 5.

---

## F. After the fix is applied

**F.1** `git_log -n 5 --path=<modified file>` — confirm the fix landed.

**F.2** Launch a new triangle round (`roborsi bench-lh skill <task>`) — Python module cache means the fix only takes effect in a fresh process.

**F.3** On the next round, return to **A** with the new run_id. If the SAME failure mode persists, the fix didn't address the right thing — restart from C/D/E rather than re-proposing the same change.

# Failure modes of this checklist itself

- Skipping section A. Without checking judge-vs-outcome contradiction first, you'll waste time tweaking the recipe when the gate is broken.
- Treating C as binary. Re-read carefully: there are 6 sub-cases that go to 4 different sections.
- Applying E without confirming D's `ok=True success=False holding_visual=False` pattern. If `ok=False`, the bug is in IK/planning (section D), NOT base-skill choice.
- Reading `_base_task.py` once and forgetting the asset metadata (E.2). Without contact_points_pose count + indices, you can't pick `contact_point_id`.
- Proposing without surveying existing base skills (E.3). Duplicates get REJECTed.

# Related

- `review_selfevo_proposal` — the next-step procedure after this checklist produces a proposal.
- `expert_trajectory_reflection` — earlier, less structured version of section E. This SKILL.md supersedes it (longer, more steps, but each step has explicit tool + expected reading).
- `pick_actor_by_contact_point` / `describe_scene_actors` — the worked-example base skills from the 2026-06-02 case.
