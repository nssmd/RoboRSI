---
name: descend_tcp_to_z
kind: base
robot: libero
category: control
version: 0.1.0
description: Drive the end-effector straight down (holding x/y) until it reaches a target world z. Closed-loop;
  holds the current gripper state.
args:
  target_z:
    type: float
    required: true
    description: World z the grip site must reach.
  x:
    type: float
    description: 'World x to hold during descent (default: current).'
  y:
    type: float
    description: 'World y to hold during descent (default: current).'
  gripper:
    type: string
    enum:
    - open
    - close
    - keep
    description: Gripper state to hold (default keep).
  max_iters:
    type: int
    description: Servo step cap (default 60).
returns:
  ok: bool
  reached: bool
  ee_pos: list
when_to_use: |
  Precise vertical placement/approach when you already know the x/y and only
  need to control height (e.g. lower onto a surface at an exact z).
metadata:
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# descend_tcp_to_z

Vertical servo of the grip site to a target z, holding x/y.
