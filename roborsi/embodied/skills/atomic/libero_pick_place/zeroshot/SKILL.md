---
name: libero_pick_place.zeroshot
kind: atomic_subskill
parent: libero_pick_place
phase: zeroshot
version: 0.1.0
description: VLM uses camera-grounded base/libero tools to attempt libero_pick_place zero-shot; the harness records the final simulator verdict.
metadata:
  tags: [zeroshot, vlm, sim, libero]
  base_tools: [look, find_pixel, unproject_pixel, grasp_object, place_object_in, move_to_pose, gripper]
params:
  episodes:    { type: int, default: 1 }
  seed_start:  { type: int, default: 0 }
  tool_budget: { type: int, default: 30 }
  backend:     { type: string, default: "libero" }
  task:        { type: string, default: "libero_object/0" }
---

# libero_pick_place.zeroshot

VLM drives the `base/libero` muscle (`look` / `find_pixel` → `grasp_object` →
`place_object_in`) against a LIBERO task. Final success is recorded only after
the episode by the harness.
