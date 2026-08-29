---
name: libero_direct_manipulation.zeroshot
kind: atomic_subskill
parent: libero_direct_manipulation
phase: zeroshot
version: 0.1.0
description: VLM uses current RGB-D and base LIBERO tools for one direct push or articulated-fixture attempt while preserving the runtime task verb.
metadata:
  tags: [zeroshot, vlm, sim, libero, pure-vision, direct-manipulation]
  base_tools: [look, find_by_pointing, pull_drawer, close_drawer, open_hinged_door, push_object]
params:
  episodes:    { type: int, default: 1 }
  seed_start:  { type: int, default: 0 }
  tool_budget: { type: int, default: 30 }
  backend:     { type: string, default: "libero" }
  task:        { type: string, default: "libero_goal/5" }
---

# libero_direct_manipulation.zeroshot

Use current camera RGB-D to localize the requested interaction points. Preserve
the runtime verb, call the matching bounded direct-manipulation tool, and inspect
a fresh image after each world-changing action. For a destination in front of a
fixture, localize the horizontal supporting surface immediately outside its
visible front edge rather than the fixture itself. Treat measured arm travel as
stage evidence only; require visible object displacement before declaring the
push complete.
