---
name: locate_object_top_surface
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Fuse visual grounding and depth to localize a visible object's top surface.
args:
  object:
    type: string
    required: true
  grid_n:
    type: int
    default: 5
  top_band_m:
    type: float
    default: 0.005
returns:
  ok: bool
  xyz: string
  chosen_label: int
  candidates: dict
  labeled_image_path: string
  note: string
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# locate_object_top_surface / RoboTwin

Fuse visual grounding and depth to localize a visible object's top surface.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
