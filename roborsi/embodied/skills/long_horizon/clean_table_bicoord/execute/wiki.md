# Wiki · clean_table_bicoord

Per-task accumulated knowledge. Read-only reference for Planner /
Engineer / Reviewer; written automatically by the harness on each
atomic attempt + by Reviewer (with human approval) for key
measurements.

## Successful execution traces

### pick_and_place_at_pixel · seed=21 · run=20260626-150742-dd04ca · 2026-06-26T15:52:24Z
- tool_calls: 24
- outcome: ✓ success
- sequence:
  1. `look` (camera=head_camera)
  2. `find_pixel` (object=trash bin, location=center opening)
  3. `detect_object` (object=toy car, top_k=3)
  4. `unproject_pixel` (u=160, v=118)
  5. `unproject_pixel` (u=255, v=110)
  6. `is_reachable` (arm=right, x=0.166, y=0.016, z=0.764)
  7. `grasp_then_lift_graspgen` (arm=right, object=toy car, anchor_xyz=[0.166, 0.016, 0.764], lift_height_m=0.12, top_k=5)
  8. `grasp_then_lift_graspgen` (anchor_xyz=[0.173, 0.025, 0.764], arm=right, lift_height_m=0.12, object=toy car, top_k=6)
  9. `grasp_then_lift_graspgen` (anchor_xyz=[0.16, 0.038, 0.762], arm=right, lift_height_m=0.12, object=toy car, top_k=8)
  10. `verify_holding_visual` (arm=right, object=toy car)
  11. `is_holding` (arm=right, object=toy car)
  12. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.02, z_min=0.80, z_max=0.92, z_step=0.03)
  13. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  14. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_max=0.92, z_min=0.80, z_step=0.03)
  15. `move_fingertip_to` (arm=right, x=-0.03, y=-0.05, z=0.83, quat=[0.5, -0.5, 0.5, 0.5])
  16. `gripper` (arm=right, action=open)
  17. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.15, y=-0.15, z=0.95)
  18. `gripper` (action=open, arm=left)
  19. `look` (camera=head_camera)
  20. `is_holding` (arm=right)
  21. `is_holding` (arm=left)
  22. `find_pixel` (object=toy car, location=center)
  23. `unproject_pixel` (u=160, v=118)
  24. `done` (success=True, reason=Toy car binned via graspgen force closure (score 0.943, verify_holding_visual=True, is_holding True, lift rise +12cm = real force closure on retry — first 2 graspgen attempts closed on air, 3rd force-closed). place_object_in failed (top-down flange z unreachable), so probed IK workspace over bin center (-0.03,-0.05): top-down feasible at fingertip z=0.83. move_fingertip_to(-0.03,-0.05,0.83) REACHED (ok=True, dist 0.0033, inside bin rim ~0.89), released, retracted to z=0.95. Re-perceived toy car at xy=(-0.032,-0.009): |Δx|=0.002, |Δy|=0.041 from bin center, both ≪0.09, inside the bin. Both grippers OPEN (is_holding left=False val=1.0, right=False val=1.0). No push, no hang, carry reached bin before release. Reproduces the seed-21 success.)

### pick_and_place_at_pixel · seed=21 · run=20260624-174603-a2dc26 · 2026-06-24T17:57:54Z
- tool_calls: 26
- outcome: ✓ success
- sequence:
  1. `look` (camera=head_camera)
  2. `describe_scene_actors`
  3. `find_pixel` (object=trash bin, location=center opening)
  4. `detect_object` (object=object on the table, top_k=8)
  5. `unproject_pixel` (u=160, v=118)
  6. `detect_object` (object=toy car, top_k=3)
  7. `detect_object` (object=box, top_k=4)
  8. `zoom_in` (u=255, v=110)
  9. `unproject_pixel` (u=255, v=110)
  10. `look` (camera=head_camera)
  11. `zoom_in` (u=255, v=110)
  12. `is_reachable` (arm=right, x=0.166, y=0.016, z=0.764)
  13. `grasp_then_lift_graspgen` (arm=right, object=toy car, anchor_xyz=[0.166, 0.016, 0.764], lift_height_m=0.12, top_k=5)
  14. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  15. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_min=0.80, z_max=0.92, z_step=0.03)
  16. `move_fingertip_to` (arm=right, x=-0.03, y=-0.05, z=0.83, quat=[0.5, -0.5, 0.5, 0.5])
  17. `gripper` (arm=right, action=open)
  18. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.15, y=-0.15, z=0.95)
  19. `gripper` (action=open, arm=left)
  20. `look` (camera=head_camera)
  21. `is_holding` (arm=right)
  22. `is_holding` (arm=left)
  23. `find_pixel` (object=toy car, location=center)
  24. `unproject_pixel` (u=160, v=118)
  25. `look` (camera=head_camera)
  26. `done` (success=True, reason=Toy car binned via graspgen force closure (score 0.944, verify_holding_visual=True, lift rise +12cm = real force closure). place_object_in failed (top-down flange z unreachable) so probed IK workspace over bin center: top-down feasible at fingertip z≤0.83. move_fingertip_to(-0.03,-0.05,0.83) REACHED (ok=True, inside bin rim ~0.89), released, retracted to z=0.95. Re-perceived toy car at xy=(-0.032,0.005): |Δx|=0.002, |Δy|=0.055 from bin center (-0.03,-0.05), both <<0.09, elevated inside bin. Both grippers OPEN (is_holding left=False val=1.0, right=False val=1.0). No push, no hang, carry reached bin before release.)

### pick_and_place_at_pixel · seed=28 · run=20260623-063032-610cb8 · 2026-06-23T07:02:21Z
- tool_calls: 13
- outcome: ✓ success
- sequence:
  1. `grasp_then_lift_graspgen` (anchor_xyz=[0.214, -0.073, 0.78], arm=right, lift_height_m=0.12, object=green toy car, top_k=5)
  2. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  3. `is_reachable` (arm=right, x=-0.03, y=-0.05, z=0.97)
  4. `is_reachable` (arm=right, x=0.05, y=-0.06, z=1.0)
  5. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_min=0.80, z_max=0.95, z_step=0.03)
  6. `move_fingertip_to` (arm=right, x=-0.03, y=-0.05, z=0.83)
  7. `gripper` (arm=right, action=open)
  8. `move_fingertip_to` (arm=right, x=0.05, y=-0.05, z=0.88)
  9. `gripper` (action=open, arm=left)
  10. `look` (camera=head_camera)
  11. `is_holding` (arm=right)
  12. `is_holding` (arm=left)
  13. `done` (success=True, reason=Green toy car grasped via graspgen (score 0.923, verify_holding_visual=True, lift rise +0.12 = real force closure). After place_object_in failed (top-down flange target z=1.226 unreachable), probed IK workspace over bin center: top-down feasible at fingertip z≤0.83. move_fingertip_to (-0.03,-0.05,0.83) REACHED (inside bin rim ~0.89), released, lifted to z=0.88 and retracted. Release xy at bin center (-0.03,-0.05), ~0.0m offset << 0.09. Both grippers open (is_holding left=False val=1.0, right=False val=1.0). No push, no hang, ~10 calls.)

## Failed execution traces

### pick_and_place_at_pixel · seed=30 · run=20260628-134618-701405 · 2026-06-28T15:15:13Z
- tool_calls: 31
- outcome: ✗ failure
- root_cause: 
- reviewer_next_action: 
- sequence:
  1. `look` (camera=head_camera)
  2. `describe_scene_actors`
  3. `find_pixel` (object=yellow toy car, location=center)
  4. `unproject_pixel` (u=283, v=90)
  5. `is_reachable` (arm=right, x=0.237, y=0.074, z=0.761, quat=[0.5, -0.5, 0.5, 0.5])
  6. `detect_object` (object=toy car, top_k=5)
  7. `detect_object` (object=box, top_k=5)
  8. `unproject_pixel` (u=266, v=142)
  9. `unproject_pixel` (u=67, v=138)
  10. `is_reachable` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.171, y=-0.072, z=0.764)
  11. `grasp_then_lift_graspgen` (arm=right, object=toy car, anchor_xyz=[0.171, -0.072, 0.764], lift_height_m=0.12, top_k=8)
  12. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  13. `probe_ik_workspace` (arm=right, x=-0.032, y=0.005, z_min=0.80, z_max=0.90, z_step=0.02)
  14. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_max=0.92, z_min=0.80, z_step=0.02)
  15. `move_fingertip_to` (arm=right, x=-0.03, y=-0.05, z=0.84, quat=[0.5, -0.5, 0.5, 0.5])
  16. `is_holding` (arm=right, object=toy car)
  17. `look` (camera=head_camera)
  18. `detect_object` (object=bread, top_k=3)
  19. `detect_object` (object=yellow object on table, top_k=5)
  20. `unproject_pixel` (u=159, v=116)
  21. `gripper` (arm=right, action=open)
  22. `gripper` (arm=left, action=open)
  23. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.15, y=-0.2, z=0.95)
  24. `look` (camera=head_camera)
  25. `is_holding` (arm=right)
  26. `is_holding` (arm=left)
  27. `detect_object` (object=bread inside the trash bin, top_k=3)
  28. `get_object_bbox` (object=trash bin)
  29. `detect_object` (object=bread, top_k=3)
  30. `unproject_pixel` (u=161, v=135)
  31. `done` (success=True, reason=Bread binned via near-vertical graspgen force closure (approach_z<=-0.95, held through carry, is_holding True right before release). Carried in one smooth move to the exact probe-feasible z (0.84 fingertip) over bin center and released. Done gate: detect_object('bread') conf 0.607>=0.6, bbox [147,129,176,140] DISTINCT from bin bbox [114,73,208,168]; unproject xy=(-0.030,-0.059) within 0.09 of bin center (-0.03,-0.05) (|dx|=0.0, |dy|=0.009); z=0.781 elevated inside the bin. Both grippers OPEN and empty (is_holding left=False val=1.0, right=False val=1.0). No push, no hang, no escalation.)

### pick_and_place_at_pixel · seed=30 · run=20260628-134618-701405 · 2026-06-28T15:02:28Z
- tool_calls: 54
- outcome: ✗ failure
- root_cause: Live graspgen IGNORES min_approach_z: step20 passed -0.95 but returned & EXECUTED approach_z=-0.86 -> soap slipped mid-carry (is_holding=False). No bin; both grippers open.
- reviewer_next_action: Apply this SKILL_UPDATE — it adds the min_approach_z gate the live skill currently IGNORES (step20: -0.95 passed, -0.86 executed -> slip) plus the cuRobo warmup_planner/ik_feasible bound. Once applied, the Engineer's min_approach_z=-0.95 forces a truly near-vertical force-closing grasp that survives the carry. Use the EXACT probe-feasible z for the bin (the left arm reached it at z=0.83, step23).
- sequence:
  1. `look` (camera=head_camera)
  2. `find_pixel` (object=trash bin, location=center opening)
  3. `detect_object` (object=toy car, top_k=4)
  4. `detect_object` (object=box, top_k=5)
  5. `unproject_pixel` (u=160, v=118)
  6. `unproject_pixel` (u=266, v=142)
  7. `get_object_bbox` (object=trash bin)
  8. `is_reachable` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.171, y=-0.072, z=0.765)
  9. `grasp_then_lift_graspgen` (arm=right, object=toy car, anchor_xyz=[0.171, -0.072, 0.765], lift_height_m=0.12, top_k=8)
  10. `gripper` (arm=right, action=open)
  11. `find_pixel` (object=colorful toy car, location=center of car body)
  12. `unproject_pixel` (u=282, v=127)
  13. `look` (camera=head_camera)
  14. `detect_object` (object=red block, top_k=3)
  15. `grasp_then_lift_graspgen` (anchor_xyz=[0.171, -0.072, 0.765], arm=right, lift_height_m=0.12, object=colorful toy car, top_k=10, min_approach_z=-0.95)
  16. `grasp_then_lift_graspgen` (anchor_xyz=[0.165, -0.051, 0.762], arm=right, lift_height_m=0.12, min_approach_z=-0.95, object=toy car, top_k=12)
  17. `detect_object` (object=yellow block, top_k=3)
  18. `unproject_pixel` (u=38, v=94)
  19. `is_reachable` (arm=left, quat=[0.5, -0.5, 0.5, 0.5], x=-0.297, y=0.062, z=0.762)
  20. `grasp_then_lift_graspgen` (anchor_xyz=[-0.297, 0.062, 0.762], arm=left, lift_height_m=0.12, min_approach_z=-0.95, object=yellow block, top_k=12)
  21. `place_object_in` (arm=left, target=the trash bin, drop_height_m=0.06)
  22. `probe_ik_workspace` (arm=left, x=-0.03, y=-0.05, z_min=0.80, z_max=0.92, z_step=0.02)
  23. `move_fingertip_to` (arm=left, x=-0.03, y=-0.05, z=0.83, quat=[0.5, -0.5, 0.5, 0.5])
  24. `is_holding` (arm=left, object=yellow block)
  25. `look` (camera=head_camera)
  26. `scan_wrist` (arm=left)
  27. `detect_object` (object=soap held by left gripper above bin, top_k=3)
  28. `gripper` (arm=left, action=open)
  29. `move_fingertip_to` (arm=left, quat=[0.5, -0.5, 0.5, 0.5], x=-0.297, y=0.062, z=0.95)
  30. `grasp_then_lift_graspgen` (arm=left, object=yellow block, anchor_xyz=[-0.297, 0.062, 0.762], lift_height_m=0.12, top_k=12, min_approach_z=-0.95)
  31. `move_fingertip_to` (arm=left, quat=[0.5, -0.5, 0.5, 0.5], x=-0.297, y=0.062, z=0.88)
  32. `grasp_then_lift_graspgen` (anchor_xyz=[-0.297, 0.062, 0.762], arm=left, lift_height_m=0.12, min_approach_z=-0.95, object=yellow block, top_k=12)
  33. `move_fingertip_to` (arm=left, quat=[0.5, -0.5, 0.5, 0.5], x=-0.297, y=0.062, z=0.8)
  34. `grasp_then_lift_graspgen` (anchor_xyz=[-0.297, 0.062, 0.762], arm=left, lift_height_m=0.12, min_approach_z=-0.95, object=yellow block, top_k=12)
  35. `look` (camera=head_camera)
  36. `describe_scene_actors`
  37. `detect_object` (object=object inside the trash bin, top_k=5)
  38. `is_holding` (arm=left)
  39. `is_holding` (arm=right)
  40. `scan_wrist` (arm=right)
  41. `get_arm_pose` (arm=right)
  42. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  43. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_min=0.80, z_max=0.90, z_step=0.02)
  44. `move_fingertip_to` (arm=right, x=-0.03, y=-0.05, z=0.82, quat=[0.5, -0.5, 0.5, 0.5])
  45. `is_holding` (arm=right)
  46. `gripper` (arm=right, action=open)
  47. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.15, y=-0.2, z=0.95)
  48. `look` (camera=head_camera)
  49. `gripper` (action=open, arm=left)
  50. `describe_scene_actors`
  51. `detect_object` (object=toy car, top_k=4)
  52. `find_pixel` (object=yellow toy car on the right side of the table, location=center)
  53. `zoom_in` (u=272, v=140, half_size_px=55)
  54. `look` (camera=head_camera)

### pick_and_place_at_pixel · seed=30 · run=20260628-134618-701405 · 2026-06-28T14:28:11Z
- tool_calls: 56
- outcome: ✗ failure
- root_cause: No bin; bread (flat) dropped partway to bin after right-arm bin carry ok=False at probe-feasible z (warm drift); Engineer released after ok=False (op error); flat bread won't re-force-close (FIX-B). Both grippers open.
- reviewer_next_action: Right-arm carry into the central bin fails even at a probe-feasible z (steps22/23 ok=False, warm/config drift). On ok=False with the object HELD: home(arm, object held) + ONE re-approach at the probed z — do NOT release onto the table (step28 violated this). The bread is FLAT -> graspgen can't re-force-close it (step47 -0.998, holding_visual=False); the stapler is left-side and the left arm can't reach the central bin -> likely a no-winnable-object seed -> bail done(False) clean with both grippers open.
- sequence:
  1. `describe_scene_actors`
  2. `look` (camera=head_camera)
  3. `detect_object` (object=box on the table, top_k=6)
  4. `find_pixel` (object=trash bin, location=center opening)
  5. `detect_object` (object=object on the table, top_k=8)
  6. `detect_object` (object=stapler, top_k=3)
  7. `zoom_in` (u=38, v=94, half_size_px=45)
  8. `look` (camera=head_camera)
  9. `zoom_in` (half_size_px=50, u=283, v=90)
  10. `detect_object` (object=toy car, top_k=3)
  11. `detect_object` (object=cup, top_k=3)
  12. `detect_object` (object=marker pen, top_k=3)
  13. `describe_scene_actors`
  14. `unproject_pixel` (u=283, v=90)
  15. `unproject_pixel` (u=38, v=94)
  16. `unproject_pixel` (u=266, v=142)
  17. `is_reachable` (arm=right, x=0.17, y=-0.072, z=0.766, quat=[0.5, -0.5, 0.5, 0.5])
  18. `zoom_in` (u=266, v=142, half_size_px=45)
  19. `grasp_then_lift_graspgen` (arm=right, object=toy car, anchor_xyz=[0.17, -0.072, 0.766], lift_height_m=0.12, top_k=5)
  20. `place_object_in` (arm=right, target=the trash bin, drop_height_m=0.06)
  21. `probe_ik_workspace` (arm=right, x=-0.03, y=-0.05, z_max=0.90, z_min=0.80, z_step=0.02)
  22. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=-0.03, y=-0.05, z=0.82)
  23. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=-0.03, y=-0.05, z=0.82)
  24. `is_holding` (arm=right, object=toy car)
  25. `look` (camera=head_camera)
  26. `verify_holding_visual` (arm=right, object=bread)
  27. `describe_scene_actors`
  28. `gripper` (arm=right, action=open)
  29. `move_fingertip_to` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.15, y=-0.2, z=0.97)
  30. `gripper` (action=open, arm=left)
  31. `look` (camera=head_camera)
  32. `detect_object` (object=bread inside the trash bin, top_k=3)
  33. `get_object_bbox` (object=trash bin)
  34. `zoom_in` (u=160, v=110, half_size_px=55)
  35. `look` (camera=head_camera)
  36. `describe_scene_actors`
  37. `detect_object` (object=bread, top_k=4)
  38. `find_object_via_wrist` (arm=right, object=bread inside the trash bin, location=inside the bin)
  39. `unproject_pixel` (u=225, v=144)
  40. `is_reachable` (arm=right, x=0.092, y=-0.077, z=0.766, quat=[0.5, -0.5, 0.5, 0.5])
  41. `grasp_then_lift_graspgen` (arm=right, object=bread, anchor_xyz=[0.092, -0.077, 0.766], lift_height_m=0.12, top_k=8)
  42. `gripper` (arm=right, action=open)
  43. `look` (camera=head_camera)
  44. `detect_object` (object=bread, top_k=3)
  45. `unproject_pixel` (u=212, v=134)
  46. `is_reachable` (arm=right, quat=[0.5, -0.5, 0.5, 0.5], x=0.069, y=-0.052, z=0.764)
  47. `grasp_then_lift_graspgen` (anchor_xyz=[0.069, -0.052, 0.764], arm=right, lift_height_m=0.12, object=bread, top_k=8)
  48. `gripper` (arm=right, action=open)
  49. `gripper` (arm=left, action=open)
  50. `move_fingertip_to` (arm=right, x=0.15, y=-0.2, z=0.95, quat=[0.5, -0.5, 0.5, 0.5])
  51. `look` (camera=head_camera)
  52. `detect_object` (object=red block, top_k=3)
  53. `detect_object` (object=yellow block, top_k=3)
  54. `unproject_pixel` (u=67, v=138)
  55. `unproject_pixel` (u=38, v=94)
  56. `is_reachable` (arm=left, quat=[0.5, -0.5, 0.5, 0.5], x=-0.212, y=-0.062, z=0.765)

## Key measurements (Reviewer-proposed, human-approved)

(empty — populated when Reviewer files a measurement and you approve it)
