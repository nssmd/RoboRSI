---
name: label_points_grid
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Overlay indexed candidate points on the current image for discrete visual selection.
args:
  grid_n:
    type: int
    default: 5
  margin_px:
    type: int
    default: 60
returns:
  ok: bool
  labeled_image_path: string
  labels: dict
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# label_points_grid / RoboTwin

Overlay indexed candidate points on the current image for discrete visual selection.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
