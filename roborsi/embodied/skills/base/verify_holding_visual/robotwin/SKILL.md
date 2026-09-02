---
name: verify_holding_visual
kind: base
robot: robotwin
version: 0.1.0
description: Confirm a grasp from the gripper's own finger separation (proprioception) after a small confirm-lift. Lifts 8cm so a slipped object falls, then reports holding from the achieved finger joint qpos — NOT vision (the old SAM/VLM pipeline detected the lifted arm as the object and false-positived empty grips). Captures the post-lift image for you to view.
metadata:
  tags: [base, perception, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"arm": "right", "object": "silver bowl"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
args:
  arm:    { type: string, required: true, enum: [left, right] }
  object: { type: string, required: false, description: "expected object name, for logging only (decision is proprioceptive)" }
returns:
  ok: bool
  holding_visual: bool     # kept for callers; == holding (now proprioceptive)
  holding: bool
  finger_opening: float
  confidence: float
  reason: str
  image_path: str
when_to_use: |
  Before calling done(success=True) on a grasp atomic, when you want a
  confirm-lift (raise 8cm; a slipped object drops out of the fingers) on top
  of the finger-width check. For a plain post-grasp gate without the lift,
  is_holding is enough — both decide from the same gripper proprioception.
---

# verify_holding_visual · RoboTwin

Raises the arm 8cm (so a slipped object falls free) and reports whether the
gripper still holds something, decided from the fingers' OWN joint separation
(same signal as is_holding) — no vision, no attach. Also snaps the head image
so you can look at the scene, but the `holding_visual` bool is proprioceptive.

The name is historical: it used to ask a VLM, which detected the lifted arm as
the object and false-positived empty grips (the move_pillbottle_pad overclaim
epidemic). Now it reads the finger encoder, which cannot be fooled that way.

