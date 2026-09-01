---
name: check_dual_arm_collision
kind: base
robot: robotwin
category: geometry
version: 0.1.0
description: Estimate clearance between both arms and attached payloads before coordinated motion.
args:
  mode:
    type: string
    required: false
    enum:
    - current
    - candidate_qpos
    - candidate_pose
  arm:
    type: string
    required: false
    enum:
    - left
    - right
  container_arm:
    type: string
    required: false
    enum:
    - left
    - right
  candidate_qpos:
    type: list
    required: false
  x:
    type: float
    required: false
  y:
    type: float
    required: false
  z:
    type: float
    required: false
  quat:
    type: list
    required: false
  attached_left:
    type: string
    required: false
    enum:
    - none
    - bowl
    - block
  attached_right:
    type: string
    required: false
    enum:
    - none
    - bowl
    - block
  clearance_threshold:
    type: float
    required: false
returns:
  ok: bool
  collides: bool
  min_clearance: float
  closest_pair: list
  h_sphere_count: int
  c_sphere_count: int
  reason: string
metadata:
  tags:
  - base
  - robotwin
  - geometry
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# check_dual_arm_collision / RoboTwin

Estimate clearance between both arms and attached payloads before coordinated motion.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
