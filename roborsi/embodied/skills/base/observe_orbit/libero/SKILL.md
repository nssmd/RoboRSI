---
name: observe_orbit
kind: base
robot: libero
category: perception
version: 0.1.0
description: Render fresh calibrated orbit RGB-D views of the current LIBERO scene and attach either one view or a labeled contact sheet.
args:
  view: { type: string, description: "Optional named orbit view. Omit for a labeled contact sheet." }
  image_size: { type: int, default: 512, description: "Per-view resolution, clamped to 256-512." }
returns:
  ok: bool
  views: list
  image_size: int
when_to_use: |
  When the normal head or wrist view leaves the target occluded or spatially
  ambiguous. Inspect the contact sheet, request one named view at full size,
  then call mark_orbit_point on a visible surface pixel in that exact view.
when_NOT_to_use: |
  Do not call repeatedly without scene motion. Orbit coordinates become stale
  after any world-changing action, so acquire a fresh view before reusing them.
metadata:
  harness:
    skip_harness: true
    skip_reason: "requires live LIBERO offscreen free-camera rendering"
---

# observe_orbit

On-demand calibrated multi-view observation. It exposes only rendered RGB-D
geometry and never task predicates, object poses, or benchmark metadata.
