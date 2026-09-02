---
name: zoom_in
kind: base
robot: robotwin
category: active_perception
version: 0.1.0
description: 4× upscaled crop of the latest image around (u, v). Replaces last_image_path so the next find_pixel/get_object_bbox runs on the zoomed view.
args:
  u: { type: int, required: true }
  v: { type: int, required: true }
  half_size_px: { type: int, default: 80, description: "crop window half-size in original-frame pixels" }
returns:
  ok: bool
  zoom_image_path: str
  zoom_window: { u0, u1, v0, v1, scale: 4 }
when_to_use: |
  When the target object is small (< 5% of image) and find_pixel has returned a
  shaky pixel. Workflow: look → find_pixel(coarse) → zoom_in(u,v) →
  find_pixel(precise) on zoomed image → map back: orig_u = u0 + u/4.
  Saves you from descending onto the wrong pixel and grasping the wrong thing.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    setup:
      skill: look
      args: {"camera": "head_camera"}
    args:
      - {"camera": "head_camera", "u": 280, "v": 120, "half_size_px": 80}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---
