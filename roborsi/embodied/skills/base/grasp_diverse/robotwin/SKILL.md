---
name: grasp_diverse
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Grasp an object by IK-prechecking an approach-angle-DIVERSE set of GraspGen candidates (vertical, angled, and side), so a reachable grasp is found even when a straight-down pose isn't feasible. Best for tall cylinders (can, bottle, roller). One call = perception + GraspGen + IK filter + try-each (hover, descend, close, verify) + lift.
args:
  arm:    { type: string, required: true, enum: [left, right, auto], description: "arm to grasp with; 'auto' tries both. Pick the arm on the object's side (object x>0 -> right, x<0 -> left)." }
  object: { type: string, required: true, description: "natural-language name of the object to grasp" }
  u:      { type: int, description: "target pixel column of the object CENTER (from find_pixel / your visual localization). REQUIRED when a distractor of the same name exists — e.g. in move_can_pot 'can' also grounds to the pot, so pass the can's (u,v) or the grasp targets the pot." }
  v:      { type: int, description: "target pixel row of the object center. Pair with u." }
returns:
  ok: bool               # True iff the grip is confirmed (held after lift)
  chosen_arm: str
  chosen_approach_z: float
  attempts: list
  reason: str
when_to_use: |
  USE for TALL / CYLINDRICAL objects — cans, bottles, rollers — whose reachable
  grasp is a SIDE grasp, not straight-down (the aloha wrist can't point straight
  down at a table reach). This is ONE of two grasp strategies:
    - grasp_top_down — steepest downward candidates (flat/short objects).
    - grasp_diverse (this) — an approach-angle spread incl. side/angled grasps.
  A good default for "pick up X" when unsure. If it returns ok=False, try
  grasp_top_down. Do NOT hand-roll a lateral grasp with move_fingertip_to — that
  is exactly the pose cuRobo can't plan.
metadata:
  harness:
    skip_harness: true
    skip_reason: "delegates to the grasp_object engine (itself skip_harness)"
---

# grasp_diverse · RoboTwin

Diverse-angle grasp. Thin wrapper: calls the `grasp_object` engine with
`strategy="diverse"`. The engine sorts GraspGen candidates by approach Z, then
IK-prechecks an evenly-spaced spread across the vertical→side range instead of the
steepest few.

Why: GraspGen often returns a cluster of near-identical straight-DOWN candidates
that the aloha wrist can't reach at a table reach (hover + grasp IK both fail). The
steepest-first strategy would burn the whole 5-slot precheck budget on those and
never try the moderate/side grasps a cylinder actually needs. Spreading the
precheck guarantees the reachable ones are checked.

If this returns `ok=False`, try `grasp_top_down` (some flat objects are only
graspable straight-down).
