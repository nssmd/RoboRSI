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
  To locate objects, disambiguate the scene, or verify a result such as whether
  an object landed in a container or a drawer opened.
---

# look

Attach a fresh camera frame to the next turn for visual inspection.
