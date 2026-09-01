---
name: detect_object
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Detect and segment objects in the current RGB observation from an open-vocabulary phrase.
args:
  object:
    type: string
    required: true
  top_k:
    type: int
  box_threshold:
    type: float
  text_threshold:
    type: float
returns:
  ok: bool
  detections: list
  best: dict
metadata:
  tags:
  - base
  - robotwin
  - perception
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# detect_object / RoboTwin

Detect and segment objects in the current RGB observation from an open-vocabulary phrase.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
