---
name: get_grasp_pose
kind: base
robot: libero
category: perception
version: 0.2.0
description: Produce top-K 6-DoF grasp candidates from a valid head-camera pixel using point-prompted
  segmentation, camera depth, and GraspGen.
args:
  pixel:
    type: list
    description: Exact object pixel [u,v] from the current head image.
  u:
    type: int
    description: Pixel column; alternative to pixel.
  v:
    type: int
    description: Pixel row; alternative to pixel.
  top_k:
    type: int
    default: 3
    description: Candidate count, clamped to 1-10.
returns:
  ok: bool
  count: int
  grasps: list
when_to_use: |
  After find_pixel identifies the intended instance, when you need to inspect
  camera-derived grasp candidates. Usually grasp_object is preferable because it
  also executes and verifies the grasp.
metadata:
  tags:
  - single-arm
  - libero
  - perception
  - grasp
  - graspgen
  - pure-vision
  backends:
  - libero
  - libero-pro
  runtime_status: code-backed
---

# get_grasp_pose

Point-prompt the current head image, unproject its depth, and run GraspGen.
