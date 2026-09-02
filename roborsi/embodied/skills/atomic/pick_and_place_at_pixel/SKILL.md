---
name: pick_and_place_at_pixel
kind: atomic
domain: manipulation
version: 0.1.0
description: Generic pick-and-place by pixel. VLM finds source object pixel, picks; finds target pixel, places. Sub-skill of any long_horizon that needs to relocate objects.
metadata:
  tags: [pick-and-place, bimanual, sim, generic]
  embodiments: [aloha-agilex]
  backends: [robotwin, robotwin-http, bicoord]
  vlm_prompts:
    describe_scene: "A tabletop with one or more objects and one or more target landing zones."
    instruction: "Pick up the named object from its current pixel, carry it, and release it onto the target pixel."
    expected_on_success: "Object visibly moved onto the target zone, gripper released."
  active_executor:
    default: zeroshot
    threshold: 0.50
params:
  source_object: { type: string, required: true, description: "name of object to pick (used as VLM prompt anchor)" }
  target_zone:   { type: string, required: true, description: "name of target landing zone" }
  arm:           { type: string, default: auto, description: "left|right|auto (pick by source x sign)" }
---

# pick_and_place_at_pixel (atomic)

Generic 2-phase pick-and-place. Used by `long_horizon` tasks that decompose
into "move object A to target X" steps.

## Lifecycle (4 件套)

| sub-skill | what |
|---|---|
| zeroshot | VLM uses `look → find_pixel(source) → move_to_pixel(grasp) → look → find_pixel(target) → move_to_pixel(release) → done`. |
| train | LeRobot v3 dataset + ACT/π₀ finetune. |
| eval | Held-out seeds; flips active_executor at threshold. |
| reset_success | Sim: env.reset(next_seed). |
| reset_failure | Sim: env.reset; mode = inferred from where it failed. |

## Notes

This is the **building block of long_horizon orchestration**. A long_horizon
plan emits steps like `{atomic: pick_and_place_at_pixel, source_object, target_zone}`;
the long_horizon executor calls into this atomic's `zeroshot/policy.py` per step.
