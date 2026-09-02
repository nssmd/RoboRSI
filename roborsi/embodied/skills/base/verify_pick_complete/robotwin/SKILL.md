---
name: verify_pick_complete
kind: base
category: robotwin
domain: perception
version: 0.1.0
description: One-call gate for "did I really pick this object?" combining proprioceptive is_holding with visual verify_holding_visual. Returns ok=True only if both checks pass.
args:
  arm:
    type: string
    enum: [left, right]
    required: true
    description: which arm should be holding the object
  object:
    type: string
    required: true
    description: noun phrase for the picked object (e.g. 'silver bowl')
  min_visual_confidence:
    type: float
    default: 0.7
    description: minimum VLM confidence for the visual holding check
metadata:
  tags: [base, perception, verify, pick, done_gate]
  embodiments: [aloha-agilex]
  backends: [robotwin, bicoord]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [1]
    setup:
      skill: pick_actor_by_contact_point
      args: {"arm": "right", "actor_name": "cup_2", "contact_point_id": 0}
    args:
      - {"arm": "right", "object": "silver bowl"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---

# verify_pick_complete

Use BEFORE `done(success=True)` in any pick atomic. Replaces the
two-step "is_holding → verify_holding_visual" discipline with a single tool call.

Returns:
  {
    ok: bool,                # all enabled checks passed → safe to done(True)
    geometric: bool,         # gripper joint width in held range
    gripper_width: float|None,
    visual: bool|None,       # VLM yes/no on wrist camera
    visual_confidence: float|None,
    reason: str
  }

Args:
  arm: "left" | "right"
  object: noun phrase, e.g. "silver bowl"
  min_visual_confidence: float (default 0.7)

If ok=False, DO NOT call done(success=True). Read `reason` to decide
whether to retry the grasp or give up.
