---
name: read_joint_state
kind: base
robot: robotwin
category: state
version: 0.1.0
description: Read the current arm and gripper joint state.
args: {}
returns:
  left_arm: string
  left_gripper: float
  right_arm: string
  right_gripper: float
metadata:
  tags:
  - base
  - robotwin
  - state
  backends:
  - robotwin
  runtime_status: requires_robotwin_backend
---

# read_joint_state / RoboTwin

Read the current arm and gripper joint state.

This is a parameterized public interface contract. The RoboTwin runtime implementation is not bundled in the current release.
