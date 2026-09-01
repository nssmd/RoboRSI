---
name: push_object
kind: base
robot: libero
category: control
version: 0.1.0
description: Pure-vision bounded planar push from a fresh object pixel toward a fresh target pixel in
  the same current RGB-D frame. The gripper closes in free space and never grasps the object.
args:
  object:
    type: string
    required: true
    description: Exact visible name of the loose object to push.
  source_pixel:
    type: list
    required: true
    description: Fresh current-frame object pixel [u, v] from find_by_pointing.
  target_pixel:
    type: list
    required: true
    description: Fresh current-frame final destination on the support surface [u, v], never a fixture
      body or an intermediate halfway point.
  max_distance:
    type: float
    default: 0.2
    description: Maximum bounded planar push distance in meters.
  standoff:
    type: float
    default: 0.06
    description: Free-space distance behind the object before contact.
  hover_clearance:
    type: float
    default: 0.08
    description: Vertical free-space clearance in meters.
  contact_z_offset:
    type: float
    default: 0.015
    description: Grip-site height offset from the measured object surface.
returns:
  ok: bool
  reached: bool
  pushed_distance: float
  reason: string
when_to_use: Use when the task explicitly says push or slide a loose object across a support surface.
when_NOT_to_use: Do not use for pick/place, attached fixtures, vertical motion, stale pixels, or while
  holding an object.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# Push Object

Localize both points in one current frame. Long contact travel is split into
bounded short segments. The tool reports measured arm travel, which is stage
evidence rather than proof that the object moved; inspect a fresh image for
visible object displacement.
