---
name: capture_image
kind: base
robot: robotwin
version: 0.1.0
description: Capture an RGB (and optionally depth) frame from a named camera in the active RoboTwin scene.
metadata:
  tags: [base, perception, sim, robotwin]
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"camera": "head_camera"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ['ok']
      min_seeds_passing: 1
params:
  env:        { type: object, required: true, description: "Active RoboTwinEnv handle." }
  camera:     { type: string, default: head_camera }
  with_depth: { type: bool,   default: false }
  save_to:    { type: string, description: "Optional path; if set, write JPEG and return path." }
returns:
  rgb:    "ndarray HxWx3 uint8"
  depth:  "ndarray HxW float32 (mm), only when with_depth=true"
  path:   "str, only when save_to is set"
---

# capture_image · RoboTwin

Snap a frame from one of the four RoboTwin cameras (`head_camera`, `front_camera`, `left_camera`, `right_camera`).

## VLM tool form

```json
{"tool": "look", "args": {"camera": "head_camera"}}
```

(VLM tool name is `look`; this skill is what the dispatcher actually calls.)

## When to use

- Always first step in a zeroshot loop: VLM has nothing to reason on without an image.
- Before `find_pixel`-style perception: provides the canvas.
- After each motion, to see what changed.

## Notes

- Returns RGB as a numpy array; `save_to` makes it a JPEG on disk for VLM round-trip.
- Depth is in millimetres; only valid when sim is configured with `data_type.depth=true`.
