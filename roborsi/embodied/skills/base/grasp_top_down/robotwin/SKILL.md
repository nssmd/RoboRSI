---
name: grasp_top_down
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Grasp an object with a straight-DOWN (top-down) approach. Strategy wrapper over the grasp_object engine that IK-prechecks the steepest downward GraspGen candidates first. Best for flat or short objects. One call = perception + GraspGen + IK filter + try-each (hover, descend, close, verify) + lift.
args:
  arm:    { type: string, required: true, enum: [left, right, auto], description: "arm to grasp with; 'auto' tries both. Pick the arm on the object's side (object x>0 -> right, x<0 -> left)." }
  object: { type: string, required: true, description: "natural-language name of the object to grasp" }
  u:      { type: int, description: "target pixel column of the object CENTER (from find_pixel / your visual localization). REQUIRED when a distractor of the same name exists, else the grasp may target the wrong object." }
  v:      { type: int, description: "target pixel row of the object center. Pair with u." }
returns:
  ok: bool               # True iff the grip is confirmed (held after lift)
  chosen_arm: str
  chosen_approach_z: float
  attempts: list
  reason: str
when_to_use: |
  USE for FLAT or SHORT objects that a straight-down grasp suits — blocks, pads,
  lids, buttons, items lying flat. This is ONE of two grasp strategies:
    - grasp_top_down (this) — steepest downward candidates.
    - grasp_diverse         — an approach-angle spread incl. side/angled grasps.
  If this returns ok=False (common for tall cylinders like cans/bottles, whose
  reachable grasp is a SIDE grasp the wrist can't do straight-down), try
  grasp_diverse. Do NOT hand-roll a lateral grasp with move_fingertip_to.
metadata:
  harness:
    skip_harness: true
    skip_reason: "delegates to the grasp_object engine (itself skip_harness)"
---

# grasp_top_down · RoboTwin

Straight-down grasp. Thin wrapper: calls the `grasp_object` engine with
`strategy="top_down"`, which sorts GraspGen candidates by approach Z and
IK-prechecks the steepest DOWNWARD ones first.

Best for flat/short objects. For a tall cylinder (can, bottle, roller) a pure
top-down pose is usually kinematically unreachable at a table reach — if this
returns `ok=False`, switch to `grasp_diverse`, which also tries the side/angled
grasps such objects actually need.
