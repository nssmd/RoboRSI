---
name: get_grasp_pose
kind: base
robot: libero
category: perception
version: 0.2.1
description: Produce top-K 6-DoF grasp candidates from a valid head-camera pixel using point-prompted segmentation, camera depth, and GraspGen.
args:
  pixel: { type: list, description: "Exact object pixel [u,v] from the current head image." }
  u: { type: int, description: "Pixel column; alternative to pixel." }
  v: { type: int, description: "Pixel row; alternative to pixel." }
  top_k: { type: int, default: 3, description: "Candidate count, clamped to 1-10." }
returns:
  ok: bool
  count: int
  grasps: "List of records: score is a finite GraspGen score; pos is [x,y,z] of the GraspGen TCP in world meters; quat is a finite [x,y,z,w] quaternion mapped to the LIBERO Franka eef target orientation; approach_z is the world-Z component of the raw GraspGen approach axis. These are unexecuted candidates, not reachability/contact/holding guarantees."
when_to_use: |
  After find_pixel identifies the intended instance, when you need to inspect
  camera-derived grasp candidates. Usually grasp_object is preferable because it
  also executes and verifies the grasp.
metadata:
  tags: [single-arm, libero, perception, grasp, graspgen, pure-vision]
---

# get_grasp_pose

Point-prompt the current head image, unproject its depth, and run GraspGen.

The backend uses GRASPGEN_HOST (localhost by default) and GRASPGEN_PORT (5556 by default). An explicitly empty GRASPGEN_PORT disables inference. The depth-only fallback has no 6-DoF rotation and cannot yield an ok=true result from this tool. Candidate TCP positions and eef orientations must be interpreted in their declared reference frames; a caller must establish the appropriate control reference and motion checks before using them. grasp_object retains its own execution and hold verification.
