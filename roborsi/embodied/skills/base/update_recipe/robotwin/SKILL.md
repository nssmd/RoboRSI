---
name: update_recipe
kind: base
robot: robotwin
category: meta
version: 0.1.0
description: Rewrite the Recipe section of this atomic's plan.md. The ONLY section of plan.md that Engineer is allowed to modify directly (Goal / Hard rules / Done gate / Success criteria are immutable without a Reviewer proposal). Use when the original Recipe's hardcoded choice fails in reality (e.g. "Recipe said arm=left but left can't reach — switch to right"). The new Recipe applies to all FUTURE attempts of this atomic; the current attempt continues using whatever's already in your context.
args:
  new_recipe: { type: string, required: true, description: "Full replacement text for the Recipe section (NOT a diff). Must be valid markdown — typically a numbered list of concrete tool calls. Will be written under the '## Recipe' heading verbatim." }
  reason: { type: string, required: true, description: "One-sentence why — what you learned that made the old Recipe wrong (cited in the next attempt's context)." }
returns:
  ok: bool
  plan_md_path: string
  reason_logged: string
when_to_use: |
  Mid-attempt, when you've executed the original Recipe's first 1-3 steps and
  hit a deterministic geometric / IK failure that a different concrete choice
  would resolve (different arm, different approach quat, different fallback
  skill). Examples:
    - "Recipe step 3 says pick_actor_by_contact_point with right arm but
       right arm IK refused at all 4 contact_point_id values → rewrite
       Recipe to use left arm starting step 3."
    - "Recipe step 4 says grasp_then_lift_graspgen but graspgen returned no
       force-closure candidates 3 times → rewrite Recipe to use the lateral
       approach skill."
  Do NOT use this to:
    - Soften the Done gate (those are immutable, propose_skill_update instead).
    - Change what the atomic is trying to achieve (that's Goal, also immutable).
    - Skip verification before done (Hard rules forbid).
when_NOT_to_use: |
  - On the FIRST attempt before you've tried anything (Recipe might be right;
    do at least one step first to learn).
  - When the failure is sim-state related (object knocked off table) not
    Recipe related — those should be reported in done(success=False, reason=...)
    so LHExecutor restores state and retries the SAME Recipe.
metadata:
  harness:
    skip_harness: true
    skip_reason: "meta tool — modifies workspace plan.md, no sim interaction. Correctness validated by next attempt actually reading the rewritten Recipe."
---

# update_recipe · robotwin

Implementation in `policy.py::dispatch_runtime`. Auto-discovered by
`_dispatch` in `rollout_runtime.py` — no rollout_runtime edit required.

Edits `<workdir>/../../plan.md` (the atomic's plan.md) by replacing
everything between the `## Recipe` heading and the next `## ` heading
(or EOF). Records `reason` to a sibling `recipe_revisions.log` so
operators see why the Recipe changed and which attempt triggered it.

Engineer can call this any number of times within an attempt — only
the final version persists. The current attempt's Engineer context
is not retroactively patched (that would invalidate already-issued
tool calls); the new Recipe is fully in effect from the NEXT attempt.
