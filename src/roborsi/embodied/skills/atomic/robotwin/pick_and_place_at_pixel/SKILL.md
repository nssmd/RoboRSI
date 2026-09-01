---
name: pick_and_place_at_pixel
kind: atomic
parent: robotwin_pick_place
domain: manipulation
version: 0.1.0
description: Move an object between source and target pixels from current vision.
metadata:
  tags: [atomic, visual-grounding, pick-place, robotwin]
  embodiments: [aloha-agilex]
  backends: [robotwin]
  runtime_status: requires_robotwin_backend
  benchmark:
    suite: robotwin
    task_key: pick_and_place_at_pixel
  vlm_prompts:
    instruction: Ground the source and target in the current image, unproject both observations, then grasp, transport, and release the source at the target.
    expected_on_success: The source object is visibly resting at the selected target.
---

# pick_and_place_at_pixel

Task profile for pixel-grounded object relocation.
