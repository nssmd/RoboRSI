# Initial plan: LIBERO task execution

## Scope and source
This is generic cold-start guidance, derived from the shipped
`libero_pick_place` plan. It is not an execution trace, a verified solution to
the current task, or evidence that any step has already succeeded. The current
runtime instruction and the available public skill contracts are authoritative.
Use a task-specific Manager-approved plan when one exists. Do not import another
task's object identities, coordinates, placement relation, or completion claims.

## Goal
Satisfy every relation in the current instruction using current observations.
First identify whether the task requires transport, drawer manipulation, another
articulated action, or several of these. A pick-and-place recipe is applicable
only to the transport portion of the task.

## Planning and execution
1. Observe the scene using the available public perception tools. Identify the
   requested objects, target and relation from the instruction and observation.
   Resolve ambiguity before acting; do not infer a destination from a task ID.
2. Write an ordered plan for the current task. Name only skills present in the
   current catalog, and use their documented arguments and preconditions.
   Drawer or articulated actions need their own observed-state subgoals; do not
   replace them with grasp-and-place merely because of the atomic task name.
3. For a transport subgoal, localize the source in the current view and use the
   available grasp skill. Check the returned physical and visual hold evidence
   before transport. A tool's top-level `ok=True` alone does not prove a hold.
4. Refresh target localization after motion. For an inside relation use the
   available container-placement skill; for an exposed support use the available
   surface-placement skill. Read that skill's contract for required evidence,
   coordinate frame and parameters. Do not reuse a stale pre-motion pixel.
5. After each action, record what its public return and current observation
   actually establish. If the grasp, motion, release or relation is unconfirmed,
   keep that subgoal unresolved. An Engineer summary or `done` call is not an
   observation and does not establish completion.
6. On failure, inspect the reported cause and current scene. Replan from the
   failed subgoal with a specific change supported by this evidence. Avoid
   repeating an unchanged failed action or spending rounds only declaring done.
   Preserve already-established state only when the evidence still applies.
7. Check all requested relations using available public observations and checks.
   For transport, verify release and supported placement, not merely a lifted
   object or an empty gripper. Declare completion only when evidence supports
   the current task's full instruction.

## Refinement and persistence
Keep this seed read-only during execution. Write the concrete per-episode plan
and revisions in the episode workspace. Reviewer diagnoses can propose plan or
skill changes; Manager reviews them and may revise them. A persistent task plan
is promoted through the existing Manager process. Skill-code publication keeps
its review and simulation gate. Failed public observations remain available for
repair; no private simulator predicates are planning inputs.

## Budget
Use the run's existing tool and round budgets. Reserve enough budget for final
relation checks. Do not add a new fixed retry count or assume a universal action
count for different LIBERO tasks.
