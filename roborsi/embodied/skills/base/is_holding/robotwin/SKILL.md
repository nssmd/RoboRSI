---
name: is_holding
kind: base
robot: robotwin
category: perception
version: 0.2.0
description: Check whether the named arm's gripper holds an object — pure proprioception, no vision. Decides from the gripper's real finger separation (achieved joint qpos) plus command intent. told-to-close AND fingers still wedged open → holding; fingers met (opening≈0) → grabbed air; told-to-open → not gripping. Uses achieved qpos because get_gripper_val reads 0 even when holding.
args:
  arm: { type: string, required: true, enum: [left, right] }
  object: { type: string, description: "(optional, not used by the decision) expected object name, for logging only." }
returns:
  ok: bool
  arm: str
  holding: bool
  finger_opening: float   # achieved finger separation (rad); ~0 empty, ~0.038 a can
  gripper_cmd: float      # command intent: ~0 told-to-close, ~1 told-to-open
  interpretation: str
when_to_use: |
  IMMEDIATELY after every grasp attempt — move_to_pixel(action='grasp'),
  gripper(close), or as a precondition check at the start of an atomic.
  The returned `holding` bool is the AUTHORITATIVE answer: it reads the
  gripper's own finger joints, so no separate verify_holding_visual call is
  needed for gating. Likewise after release — holding=false confirms the
  object actually left the gripper.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: []
      min_seeds_passing: 1
---

# is_holding · RoboTwin

The grasp-success oracle — decided from the gripper's OWN finger joints, no
vision, no attach. After every grasp/release, call this:

- told-to-close (gripper_cmd≈0) AND fingers wedged open (finger_opening>0.01):
  holding=true — an object is between the fingers.
- told-to-close AND fingers met (finger_opening≈0): holding=false — grabbed air.
- told-to-open (gripper_cmd≈1): holding=false — the gripper isn't gripping.

`finger_opening` is the achieved joint separation (a can wedges the fingers to
~0.038 rad). This is read from the articulation qpos, NOT get_gripper_val —
that returns the commanded target (0 on any close) and reads 0 even while
holding, which is why the old visual fallback existed and false-positived.

The `holding` field can be trusted directly for precondition gating between
bimanual atomic transitions.

