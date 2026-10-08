---
name: is_holding
kind: base
robot: libero
category: perception
version: 0.1.0
description: Estimate whether the gripper is holding an object from the Panda finger opening, distinguishing closed-empty, holding, and fully-open states.
args:
  object: { type: string, description: "Optional expected-object label, retained in the result for trace readability; it does not change the proprioceptive check." }
returns:
  ok: bool
  holding: {type: bool, description: "True only for gripper_state=held. False also covers ambiguous and is not by itself proof of an empty gripper."}
  gripper_gap: float
  gripper_state:
    type: string
    enum: [open, closed_empty, held, ambiguous]
    description: "Exact LIBERO literals are open, closed_empty, held, ambiguous. The calibrated open endpoint is serialized as open, NOT fully_open. Ambiguous must not be treated as empty or a verified release; this proprioceptive signal does not prove object identity or containment."
when_to_use: |
  After a grasp as a proprioceptive signal before transporting, or during
  recovery. For thin objects, combine the raw gap with visible scene evidence.
---

# is_holding

Grasp-state estimate using gripper proprioception only.
