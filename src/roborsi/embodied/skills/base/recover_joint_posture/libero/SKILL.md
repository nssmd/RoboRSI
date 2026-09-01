---
name: recover_joint_posture
kind: base
robot: libero
category: control
version: 0.1.0
description: Recover an empty-gripper arm from a folded or singular posture by returning toward the episode's
  captured initial joint state.
args:
  max_iters:
    type: int
    default: 240
    description: Bounded JOINT_POSITION recovery steps.
returns:
  ok: bool
  reached: bool
  joint_error_max: float
when_to_use: |
  After multiple Cartesian motion and home calls make zero progress and
  is_holding confirms that the gripper is empty. The target is captured from
  this episode's initial robot proprioception, not from a task demonstration.
when_NOT_to_use: |
  Do not call while holding an object. Place the object safely first. Do not
  use as a normal transport primitive.
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# recover_joint_posture

Pure-proprioceptive joint-space escape for Cartesian deadlocks.
