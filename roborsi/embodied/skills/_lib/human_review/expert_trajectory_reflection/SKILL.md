---
name: expert_trajectory_reflection
kind: meta_skill
description: After N rounds of failure on the same atomic where the VLM is following the recipe correctly but sim says success=False, agent reads the BiCoord-Bench env source to find the expert trajectory's primitives, then proposes wrapping any primitive that isn't already a base skill. Codifies the 2026-06-02 finding that pick_bowl was failing because every grasp primitive ignored the asset's annotated contact_points_pose that the expert uses.
version: 1
metadata:
  tags: [self-evolution, reflection, sim, base-skill, governance, meta]
  trigger_conditions:
    - atomic has >=2 rounds of identical-mode failure
    - VLM trace shows recipe was followed (right tools called, right args)
    - sim returns ok=true success=false holding_visual=false
---

# Overview

The agent has tools to see what the sim REPORTS (judge reasons, outcomes, inner traces), but until 2026-06-02 it had no instinct to ask **"is my base skill even taking the right path?"** When generic primitives (`pick_bowl_lateral_v2`, `grasp_then_lift_graspgen`) ran to completion and reported `ok=True success=False holding_visual=False`, the agent kept tweaking prompts ("try wider rim_radius", "try 8 angles") instead of asking why the geometric grasp doesn't actually clamp. The answer was sitting in `BiCoord-Bench/envs/<task>.py`: the expert trajectory uses `grasp_actor(actor, contact_point_id=N)` — asset-annotated grasp poses that the generic primitives ignored.

This skill is the reflection protocol so the agent reaches that answer itself.

# Trigger

Run this protocol when ALL of:
1. `get_failure_patterns(atomic)` shows ≥2 SYSTEMATIC rounds at this atomic.
2. `get_inner_trace(latest_run, tool_filter=<the recipe's primary primitive>)` shows the VLM **did** call the recipe's tools (it's not a prompt/recipe ignorance issue).
3. Those primitive calls returned `ok=True, success=False, holding_visual=False` (motion ran, sim disagrees) — NOT `ok=False` (which is a tool/IK bug).

If those conditions hold, the bug is in the **base skill choice**, not the prompt.

# Phases

## 1. Find the sim task name

`get_lh_report(<lh_task>)` → look at the `sim_task` field, or read the LH executor source to see how sim_task is derived.

## 2. Read the BiCoord env source

`read_file('$ROBORSI_BICOORD_ROOT/envs/<sim_task>.py')` — there's always one. Look for:
- `play_once` or expert trajectory: it lists the primitives the expert uses (`grasp_actor`, `place_actor`, `move_to_pose`, `gripper`, `contact_point_id=...`).
- `check_success`: the actual sim success criterion (object positions within eps of target). This is the truth your skill must produce.
- `load_actors`: how objects are created (`create_actor`, `create_box`, attribute names like `self.cup_2`).

## 3. Read the asset metadata for each grasped object

`read_file('$ROBORSI_BICOORD_ROOT/assets/objects/<modelname>/model_data<N>.json')` (cup_id=5 → `model_data5.json`). Look for:
- `contact_points_pose`: 4×4 transforms specifying tuned grasp positions.
- `functional_matrix`, `target_pose`, `orientation_point`: other useful annotations.

If the expert uses `contact_point_id=N` and our base skill doesn't read `contact_points_pose`, that's the bug.

## 4. Survey existing base skills

`list_dir('roborsi/embodied/skills/base/robotwin')` + `grep_repo('get_grasp_pose|contact_points_pose', '*.py', 'repo')` — find out whether any existing base skill already wraps the expert's path. If not, propose a new one.

## 5. Propose wrapping the expert's primitive

`propose_new_skill(category='base/robotwin', name='<short_descriptive>', ...)` — wrap the expert's primitive (`grasp_actor`, `place_actor`, etc.) as a dispatch_runtime base skill. Reference `pick_actor_by_contact_point/policy.py` as the template.

The proposal MUST cite in its rationale:
- Which expert primitive (`grasp_actor(...)` line + file in BiCoord env).
- Which asset metadata (`contact_points_pose` in model_data*.json).
- Why generic primitives fail (motion completes but force-closure doesn't form).
- Which existing base skills DON'T cover this (`pick_bowl_lateral_v2` reaches rim but ignores contact points).

## 6. Also update the atomic's prompt

If a new base skill is created, also `propose_skill_update` on `<atomic>.zeroshot` to promote the new base skill to Step 1 of the recipe, with the old generics as fallbacks.

# Success criteria

- The newly-proposed base skill calls `env._impl.get_grasp_pose(actor, arm_tag, contact_point_id, pre_dis)` (or analogous expert primitive).
- It looks up the actor via `getattr(env._impl, actor_name)`, NOT scene name (scene names are ambiguous when multiple instances of the same model exist).
- Result includes `holding_visual` from a true `verify_holding_visual` call, no heuristic.
- The atomic's prompt names the new base skill as primary path.

# Failure modes

- Skipping condition 3 (the `ok=True success=False` check). If `ok=False`, the bug is in IK / planning, not base-skill choice — different protocol.
- Reading the wrong env file (sim_task != lh_task name). Confirm via `get_lh_report`'s `sim_task` field.
- Wrapping the wrong actor (model name vs attribute name). Always use the Python attribute name (`cup_2`), not the scene name (`002_bowl`).
- Skipping the asset metadata read — proposing a generic wrapper without contact_points_pose is no better than the existing generics.

# Related

- `review_selfevo_proposal` — the rules for accepting/rejecting the proposals this skill produces.
- `pick_actor_by_contact_point` — the worked example from the 2026-06-02 case.
- `bot_agent` system prompt: contains the lighter-weight CONTRADICTION SIGNAL protocol (used when judge and outcome disagree); this skill is the heavier-weight version for systematic-but-no-progress cases.
