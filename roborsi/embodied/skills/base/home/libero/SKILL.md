---
name: home
kind: base
robot: libero
category: control
version: 0.1.0
description: Retract the arm to a safe clearance height above its current position and open the gripper — clears the workspace between sub-tasks.
args:
  clearance_z: { type: float, description: "World z to retract the end-effector to (default 0.28)." }
returns:
  ok: bool
  ee_pos: list
when_to_use: |
  After a place, or to get the arm out of the camera's way / away from clutter
  before the next action.
---

# home

Retract end-effector straight up to a clearance height and open the gripper.
