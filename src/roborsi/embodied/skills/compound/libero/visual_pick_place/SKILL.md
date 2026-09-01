---
name: visual_pick_place
kind: compound
parent: libero_pick_place
domain: manipulation
description: >
  Code-backed one-call LIBERO pick and place onto an exposed surface. From
  current RGB/depth it points to the named source, performs a visually and
  identity verified grasp, points to the destination, and places with the
  verified exposed-surface policy.
version: 0.1.0
args:
  source: { type: string, required: true, description: "Exact movable-object phrase from the instruction." }
  target: { type: string, required: true, description: "Exact destination phrase from the instruction." }
  placement: { type: string, required: true, enum: [surface], description: "Must be surface: plate/stove/pad/stand/scale/rack." }
  source_pixel: { type: list, description: "Optional current head-image [u,v] for an ambiguous or relational source." }
  target_pixel: { type: list, description: "Optional current head-image [u,v] for an ambiguous or relational destination." }
  hover: { type: float, description: "Optional grasp hover height." }
  grasp_z_offset: { type: float, description: "Optional close-height adjustment." }
  place_hover: { type: float, description: "Optional placement approach/retract height." }
  release_clearance: { type: float, description: "Optional exposed-surface release clearance." }
  pos_tol: { type: float, description: "Optional exposed-surface position tolerance." }
  z_offset: { type: float, description: "Optional container release height." }
returns:
  ok: bool
  grasped: bool
  placed: bool
  released: bool
  failed_phase: string
  trace: list
  reason: string
when_to_use: |
  First choice for a single-object instruction whose successful route is
  grasp_object followed by place_on_surface. Use the exact source and target
  wording. The compound fails closed on an unverified held identity and never
  reads the simulator predicate.
when_NOT_to_use: |
  Do not use for containers/cavities, beside relations, drawer pulling,
  switches/knobs, stacking, multiple movable objects, or exact externally
  supplied 3-D poses.
metadata:
  tags: [compound, solidified, libero, pure-vision, pick-place]
  backends: [libero, libero-pro]
  runtime_status: code-backed
  compound: true
---

# visual_pick_place

This compound codifies a recurrent short-task path using only current visual
perception and existing code-backed LIBERO base skills. Final task success
remains post-hoc simulator adjudication.
