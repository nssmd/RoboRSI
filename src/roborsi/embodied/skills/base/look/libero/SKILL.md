---
name: look
kind: base
robot: libero
category: perception
version: 0.1.0
description: Snap an RGB frame from a camera (head/agentview or wrist) and attach it to your next turn so you can visually inspect the scene.
args:
  camera: { type: string, default: head, enum: [head, wrist], description: "head = agentview overview; wrist = eye-in-hand close-up." }
returns:
  ok: bool
  camera: string
when_to_use: |
  To locate or disambiguate an object or target in the current scene, and to
  visually verify a result (did the object land in the container? is the drawer
  open?). Use the head view for scene context and the wrist view for close-up
  inspection.
---

# look

Attach a fresh camera frame to the next turn for visual inspection.
