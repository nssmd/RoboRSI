---
name: libero_direct_manipulation
kind: atomic
domain: manipulation
version: 0.1.0
description: Pure-vision single-arm LIBERO direct manipulation for pushes and articulated fixtures; follows the runtime task verb without converting it into pick-and-place.
metadata:
  tags: [single-arm, direct-manipulation, libero, pure-vision]
  embodiments: [franka-panda]
  backends: [libero, libero-pro]
  libero_task: libero_goal/5
  vlm_prompts:
    instruction: |
      Follow the exact visible LIBERO task verb.

      - For push, slide, open, or close tasks, do not grasp or relocate the
        manipulated object unless the task explicitly says pick, put, or place.
      - Open a drawer only with pull_drawer after fresh handle localization.
      - Close a drawer only with close_drawer after fresh drawer-front or handle
        localization.
      - Open a hinged appliance door only with open_hinged_door after fresh
        handle localization and visible hinge-side selection.
      - Push a loose object only with push_object using fresh source and target
        pixels from the same current frame.
      - For a relation such as "in front of" a fixture, point to the free
        horizontal supporting surface immediately outside the fixture's visible
        front edge. Do not point on the fixture, burner, body, or halfway from
        the source object.
      - Execute long pushes as short contact segments. Re-observe afterward;
        arm travel is only stage evidence and visible object displacement is
        required before claiming that the push worked.
      - Re-observe after every world-changing action. Tool success is stage
        evidence; declare completion only from a fresh visible scene.
    expected_on_success: |
      The exact direct manipulation requested by the runtime task is visibly
      complete, with the arm clear of the target.
  active_executor:
    default: zeroshot
    threshold: 0.70
---

# LIBERO Direct Manipulation

Neutral guidance for direct pushes and articulated fixtures. Runtime task text
is authoritative; this profile contributes no scene coordinates or task
predicate.
