---
name: home
kind: base
robot: robotwin
category: control
version: 0.1.0
description: Move the robot arms to the configured neutral posture.
args: {}
returns:
  ok: bool
metadata:
  tags:
  - base
  - robotwin
  - control
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# home / RoboTwin

Move the robot arms to the configured neutral posture.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
