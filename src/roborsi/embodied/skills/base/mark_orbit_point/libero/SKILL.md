---
name: mark_orbit_point
kind: base
robot: libero
category: geometry
version: 0.1.0
description: Convert a visible pixel in a fresh observe_orbit RGB-D view into a world-frame XYZ surface
  point.
args:
  view:
    type: string
    required: true
    description: Exact view name returned by observe_orbit.
  u:
    type: int
    required: true
    description: Pixel column in the full-size named view.
  v:
    type: int
    required: true
    description: Pixel row in the full-size named view.
  mode:
    type: string
    default: surface
    enum:
    - surface
    - ray
    description: surface uses RGB-D; ray uses two distinct views for a free-space point.
  point_id:
    type: string
    description: For ray mode, reuse the point_id from the first view click.
returns:
  ok: bool
  view: string
  pixel: list
  world: list
when_to_use: |
  After observe_orbit attaches one named full-size view and the intended
  surface point is visually unambiguous. Use the returned world point with
  existing IK preview and motion tools. For a free-space target, use mode=ray
  in one view, then reuse its point_id with a second view of the same location.
when_NOT_to_use: |
  Do not mark the contact sheet itself or reuse a point after scene motion.
  This returns first-visible-surface geometry, not an inferred object center.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# mark_orbit_point

Depth-backed orbit pixel to world XYZ.
