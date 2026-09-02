---
name: plan
kind: base
robot: robotwin
category: meta
version: 0.2.0
description: |
  Declare and revise an execution plan. Rollout §III-B 4-phase pattern:
  RECEIVE_INSTRUCTION → DESCRIBE_SCENE → STEPS_PLANNING → EXECUTE.
  Each substep has progress %, success_evidence, fallback. The plan is
  RECORDED, not enforced — re-emit plan() any time during execution to
  revise based on what was learned (substeps changed, scene differs,
  new strategy). Validation is LENIENT: warns about issues but accepts
  the plan; VLM uses the warnings to refine.
args:
  goal: { type: string, required: true,
          description: "One-line task goal in your own words (RECEIVE_INSTRUCTION phase)." }
  scene_summary: { type: string, required: false,
                   description: "1-3 sentence description of what you saw via look() (DESCRIBE_SCENE phase). Strongly recommended on FIRST plan; optional on revision." }
  substeps: { type: list, required: true,
              description: "Ordered substep dicts. See schema below." }
  reason_for_revision: { type: string, required: false,
                          description: "If this is a re-plan (not the first), explain why the previous plan was wrong (failed substep, scene changed, new info). Helps debugging." }
returns:
  ok: bool
  plan_id: str
  n_substeps: int
  validation_warnings: list[str]   # non-blocking issues for VLM to consider
  total_progress_pct: int          # last substep's progress_pct (should be 100)
  is_revision: bool
when_to_use: |
  - FIRST atomic-task action: emit plan() before anything else.
  - DURING execution: re-emit plan() whenever your understanding changes
    (e.g. block discovered unreachable; perception revealed new constraint;
    you want to swap substeps order). Re-plans are LOGGED — frequent
    revisions aren't penalized, but include reason_for_revision.

substep schema: |
  Each substep dict supports the following keys (only `name` and `primary`
  are strictly required; the rest are strongly recommended):

    name             (str, required)  — snake_case short name
    primary          (str, required)  — "<tool_name>: <one-line strategy>"
    progress_pct     (int, optional)  — cumulative % when complete (0-100)
    preconditions    (list[str])      — what must be true to start
    success_evidence (str)            — what stdout / state proves success
    fallback         (str)            — alternative if primary fails
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"goal": "pick the right bowl", "substeps": [{"name": "approach", "primary": "find_object_via_wrist", "progress_pct": 50}, {"name": "grasp", "primary": "pick_actor_by_contact_point", "progress_pct": 100}]}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---


# plan · RoboTwin

## Why we have it

Without an explicit plan, VLMs grab the first tool that comes to mind from
training (often the wrong baseline). Forcing a plan FIRST makes the agent
think about strategy + fallback BEFORE training priors take over.

## 4-Phase pattern (Rollout Fig 3)

1. **RECEIVE_INSTRUCTION**  — paraphrase the goal in your own words. Surface
   ambiguities by stating what you assume.
2. **DESCRIBE_SCENE**  — call `look()` first; in `scene_summary` describe the
   visible objects, their approximate positions, and any constraints
   (other arm holding something, occlusion, etc).
3. **STEPS_PLANNING**  — emit `plan(...)` with substeps that, if all complete,
   guarantee the goal is met. Cumulative `progress_pct` should reach 100.
4. **EXECUTE**  — issue tool calls for substep[0] in your next turn.

## Replanning

The plan is mutable. Whenever you discover the existing plan is wrong:
- substep ordering needs swap
- a substep was missing
- the goal decomposition itself was incorrect

simply emit `plan(...)` again with `reason_for_revision="..."`. The new plan
replaces the old; previous progress is reset for substeps that changed.

## Substep example (beat_block_hammer)

```python
plan(
  goal="Tap hammer head onto top of red block.",
  scene_summary="Toy hammer at table center; red block 15cm to the right; "
                "both arms free; head_camera shows clear top-down view.",
  substeps=[
    {
      "name": "locate_block",
      "primary": "localize_object_top_center: 5x5 grid + sub-VLM pick",
      "progress_pct": 15,
      "preconditions": [],
      "success_evidence": "stdout shows xyz=[x,y,z] with x in [-0.25,0.25] etc",
      "fallback": "find_pixel('coloured block') + unproject_pixel"
    },
    {
      "name": "choose_arm",
      "primary": "is_reachable both arms; pick reachable side",
      "progress_pct": 25,
      "preconditions": ["block xyz known"],
      "success_evidence": "exactly one arm has reachable=True",
      "fallback": "if neither: done(success=False) immediately"
    },
    {
      "name": "grasp_hammer",
      "primary": "grasp_then_lift_graspgen with multi-query SAM",
      "progress_pct": 60,
      "preconditions": ["arm chosen"],
      "success_evidence": "actor_z rose ≥3cm AND val<0.6",
      "fallback": "grasp_then_lift baseline with manual top-down"
    },
    {
      "name": "tap_on_block",
      "primary": "tap_held_on_target with tool_query='hammer'",
      "progress_pct": 90,
      "preconditions": ["hammer held"],
      "success_evidence": "tap returns ok=True with err_xy<2cm",
      "fallback": "lower hover_m, retry"
    },
    {
      "name": "verify_done",
      "primary": "list_contacts + check hammer-block pair",
      "progress_pct": 100,
      "preconditions": ["tap completed"],
      "success_evidence": "stdout shows ('hammer','box') in contacts",
      "fallback": "done(success=False) with reason"
    },
  ]
)
```
