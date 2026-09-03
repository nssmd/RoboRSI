---
name: libero_pick_place
kind: atomic
domain: manipulation
version: 0.2.0
description: Pure-vision single-arm LIBERO pick and place. Localize the movable object and destination from current camera RGB/depth, grasp with visual evidence, and route placement by destination geometry.
metadata:
  tags: [single-arm, pick-place, libero, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  libero_task: libero_object/0
  vlm_prompts:
    instruction: |
      You control a single Franka Panda arm in a LIBERO tabletop scene.
      This is PURE VISION: locate every object and destination from the current
      head or wrist camera and camera depth. Never invent or reuse coordinates
      from another episode.

      For each pick-and-place instruction:
      0. When `visual_pick_place` is listed in the available tools, make it the
         FIRST CHOICE for one movable object going to an exposed surface or a
         visible container. Pass the exact source and target wording plus
         placement=surface for plate/stove/pad/stand/scale/rack, or
         placement=container for basket/bowl/bin/open drawer/cavity. Inspect
         failed_phase before falling back to individual tools.
      1. Inspect the current head image and identify the movable object and its
         destination. Use find_pixel and unproject_pixel when a composite skill
         needs an explicit pixel.
      2. Call grasp_object with a visually meaningful object name and, when
         available, pixel=[u,v]. Continue only when grasped=true and visual hold
         evidence was recorded. If do_not_regrasp=true, do not call grasp_object
         again and do not open the gripper.
      3. Route placement by geometry:
         - plate, stove, pad, stand, scale, exposed support: place_on_surface;
         - basket, bowl, bin, drawer, cavity: place_object_in;
         - beside relation: place_beside;
         - exact externally-derived pose: place_held_at_target_servo.
      4. Inspect the visible result before done(success=True). If a hold gate or
         placement gate fails, do not open the gripper manually.
    expected_on_success: |
      The object named by the instruction is visibly resting at the requested
      destination, the source no longer contains it, and the gripper is open.
  active_executor:
    default: zeroshot
    threshold: 0.70
---

# libero_pick_place

Pure-vision LIBERO pick and place using the current camera frame, depth,
proprioception, and the namespace-scoped base skills.
