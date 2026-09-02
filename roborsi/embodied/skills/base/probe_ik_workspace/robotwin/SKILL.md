---
name: probe_ik_workspace
kind: base
robot: robotwin
category: diagnostic
version: 0.1.0
description: |
  Diagnostic-only IK probe. Given a target world XYZ, test a SET of
  candidate wrist orientations and z heights to find which combinations
  cuRobo's IK can actually plan. Use BEFORE concluding "unreachable" —
  many V5-V19 failures came from agents trying only top-down quat and
  reporting "all IK fails" when lateral approaches were trivially
  reachable. Does NOT mutate sim state (cuRobo plan is pure compute).
args:
  arm: { type: string, required: true, enum: [left, right] }
  x: { type: number, required: true, description: "World X of target TCP (fingertip) point." }
  y: { type: number, required: true, description: "World Y of target TCP point." }
  z_min: { type: number, required: false, description: "Lowest TCP z to probe (default 0.73 — just above table)." }
  z_max: { type: number, required: false, description: "Highest TCP z to probe (default 0.95)." }
  z_step: { type: number, required: false, description: "z step (default 0.02)." }
  approaches: { type: array, required: false, description: "List of approach names to test. Defaults to all 7 canonical: ['top_down','lateral_-x','lateral_+x','lateral_-y','lateral_+y','tilt_30_-x','tilt_30_+x']." }
returns:
  ok: bool
  per_approach: object   # {approach_name: [feasible_z, ...]}
  best: object           # {approach, lowest_feasible_z} — recommend this
  summary: string        # one-line human-readable
when_to_use: |
  - BEFORE giving up on a grasp because "IK refused" — verify whether
    OTHER wrist orientations might work.
  - When Reviewer is about to write "target unreachable" — call this
    first to confirm.
  - When choosing between arms: probe both, pick the one with broader
    feasible envelope.
when_NOT_to_use: |
  - For mid-execution micro-adjustments (use is_reachable for single
    pose check; cheaper).
  - When you already know the answer (e.g. left arm with top-down on
    table-z+0.10 always works).
metadata:
  harness:
    skip_harness: true
    skip_reason: "Pure-compute diagnostic, no sim mutation. Tested via scripts/probe_bowl_ik.py."
---

# probe_ik_workspace · robotwin

Wraps cuRobo `plan_path` over a grid of (approach quat × TCP z). Each
candidate's flange pose is derived from the desired fingertip XYZ via
the appropriate TCP→flange offset (ALOHA_TCP_IN_EE_LOCAL = 0.1556m
along local +X). The mapping of local +X to world axis depends on the
wrist quat — handled in policy.py.

This skill is what `scripts/probe_bowl_im.py` does, exposed as a tool
agents can call. Calling it BEFORE a `propose_new_skill` for
"the arm can't reach X" prevents premature proposals; calling it
BEFORE a fallback strategy switch tells you which fallback is even
worth trying.

# Failure modes

- All probes infeasible → target truly outside reach (try other arm
  or move closer).
- Only feasible at high z → target reachable only "from above"; can't
  do horizontal pickup. Use top-down skills.
- Only lateral feasible → target on workspace edge / cross-midline;
  use pick_bowl_by_rim or similar.
