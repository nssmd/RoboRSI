---
name: place_two_in_container
kind: compound
parent: libero_long
domain: long_horizon
version: 0.1.0
description: Place two named objects into one visible container with a code-backed two-cycle policy.
args:
  object_a: { type: string, required: true, description: "First object named by the instruction." }
  object_b: { type: string, required: true, description: "Second object named by the instruction." }
  container: { type: string, required: true, description: "Visible destination container." }
returns:
  ok: bool
  completed: int
  trace: list
  reason: string
metadata:
  tags: [compound, solidified, libero, long-horizon, container]
  backends: [libero, libero-pro]
  runtime_status: code-backed
  compound: true
---

# place_two_in_container

Runs two ordered pure-vision grasp-and-place cycles using published Base Skills.
