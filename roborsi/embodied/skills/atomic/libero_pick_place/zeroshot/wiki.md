# Wiki · libero_pick_place

Per-task accumulated knowledge. Read-only reference for Planner /
Engineer / Reviewer.

TRUST HIERARCHY (read this before believing any entry below):
  1. Successful execution traces + Key measurements + Manager-approved leads =
     SIM-VERIFIED / Manager-approved FACTS. Trust them.
  2. Failed execution traces record OBSERVED FACTS ONLY (seed, outcome, tool
     sequence). The Reviewer's interpretation is NOT here — it is held in the
     wiki_review queue until a Manager approves it, precisely because wrong
     hypotheses (e.g. "the receiver is a decoy") have poisoned this loop before.
     An approved lead moves into 'Manager-approved leads'; nothing unreviewed
     ever steers a plan.

## Successful execution traces

### libero_pick_place · seed=1 · run=20260706-174538-8b2379 · 2026-07-06T17:50:24Z
- tool_calls: 32
- outcome: ✓ success
- sequence:
  1. `?`
  2. `?`
  3. `?`
  4. `?`
  5. `?`
  6. `?`
  7. `?`
  8. `?`
  9. `?`
  10. `?`
  11. `?`
  12. `?`
  13. `?`
  14. `?`
  15. `?`
  16. `?`
  17. `?`
  18. `?`
  19. `?`
  20. `?`
  21. `?`
  22. `?`
  23. `?`
  24. `?`
  25. `?`
  26. `?`
  27. `?`
  28. `?`
  29. `?`
  30. `?`
  31. `?`
  32. `?`

### libero_pick_place · seed=0 · run=20260706-174429-91a04c · 2026-07-06T17:45:33Z
- tool_calls: 5
- outcome: ✓ success
- sequence:
  1. `?`
  2. `?`
  3. `?`
  4. `?`
  5. `?`

### libero_pick_place · seed=0 · run=20260706-173857-30bf2d · 2026-07-06T17:43:18Z
- tool_calls: 28
- outcome: ✓ success
- sequence:
  1. `?`
  2. `?`
  3. `?`
  4. `?`
  5. `?`
  6. `?`
  7. `?`
  8. `?`
  9. `?`
  10. `?`
  11. `?`
  12. `?`
  13. `?`
  14. `?`
  15. `?`
  16. `?`
  17. `?`
  18. `?`
  19. `?`
  20. `?`
  21. `?`
  22. `?`
  23. `?`
  24. `?`
  25. `?`
  26. `?`
  27. `?`
  28. `?`

## Failed execution traces

### libero_pick_place · seed=0 · run=20260706-175030-0a1fd2 · 2026-07-06T17:52:54Z
- tool_calls: 14
- outcome: ✗ failure
- reviewer diagnosis: [PENDING REVIEW — root_cause + next_action are queued to wiki_review/1783360374-hyp-99f220; NOT shown as a lead until a Manager approves them, so an unverified guess can't steer the next plan]
- sequence:
  1. `?`
  2. `?`
  3. `?`
  4. `?`
  5. `?`
  6. `?`
  7. `?`
  8. `?`
  9. `?`
  10. `?`
  11. `?`
  12. `?`
  13. `?`
  14. `?`

## Manager-approved leads

- [20260706-175030-0a1fd2] After grasp (step9 grasped=true), run verify_holding_visual; use place_object_in with servo-centering over yellow_plate_1 and lower release z_offset (~0.03), then visually confirm bowl on plate before done.
  - root_cause: Engineer skipped verify_holding_visual gate and place_at released at z_offset=0.08 (too high) then declared done from a single look image — bowl likely not actually on plate (vlm_overclaimed).
  - approved 2026-07-06T18:04:58Z · Valid LIBERO place-precision lead (camera-only): after grasp run verify_holding_visual; place_object_in with servo-centering over the plate; lower release z_offset to ~0.03 (0.08 dropped the bowl too high -> vlm_overclaimed). Approved.

## Key measurements (Reviewer-proposed, human-approved)

(empty — populated when Reviewer files a measurement and you approve it)
