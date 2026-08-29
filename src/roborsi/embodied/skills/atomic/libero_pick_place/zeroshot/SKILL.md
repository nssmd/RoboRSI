---
name: libero_pick_place.zeroshot
kind: atomic_subskill
parent: libero_pick_place
phase: zeroshot
version: 0.2.0
description: VLM uses pure-vision base/libero tools to execute a LIBERO pick-and-place attempt from current camera RGB/depth.
metadata:
  tags: [zeroshot, vlm, sim, libero, pure-vision]
  base_tools: [look, find_pixel, unproject_pixel, grasp_object, place_on_surface, place_object_in, place_beside, place_held_at_target_servo]
params:
  episodes:    { type: int, default: 1 }
  seed_start:  { type: int, default: 0 }
  tool_budget: { type: int, default: 30 }
  backend:     { type: string, default: "libero" }
  task:        { type: string, default: "libero_object/0" }
---

# libero_pick_place.zeroshot

Use current camera RGB/depth to localize the movable object and destination.
Call `grasp_object` for a visually evidenced hold, then route exposed supports
to `place_on_surface`, containers or cavities to `place_object_in`, beside
relations to `place_beside`, and exact externally-derived poses to
`place_held_at_target_servo`.
