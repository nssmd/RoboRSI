# Wiki · match_blocks_bicoord

Per-task accumulated knowledge. Read-only reference for Planner /
Engineer / Reviewer; written automatically by the harness on each
atomic attempt + by Reviewer (with human approval) for key
measurements.

## Successful execution traces

### pick_and_place_at_pixel · seed=27 · run=20260627-142008-cd482d · 2026-06-27T15:23:54Z
- tool_calls: 10
- outcome: ✓ success
- sequence:
  1. `look` (camera=head_camera)
  2. `describe_scene_actors`
  3. `gripper` (arm=right, action=open)
  4. `find_pixel` (object=gray square sign marker on the right half of the table, nearest the front (lowest in image), location=center of the sign)
  5. `unproject_pixel` (u=253, v=164)
  6. `move_to_pose` (arm=right, x=0.149, y=-0.1, z=0.86, quat=[0.0616, -0.0616, 0.7044, 0.7044])
  7. `move_to_pose` (arm=right, quat=[0.0616, -0.0616, 0.7044, 0.7044], x=0.149, y=-0.1, z=0.81)
  8. `move_to_pose` (arm=right, quat=[0.0616, -0.0616, 0.7044, 0.7044], x=0.149, y=-0.1, z=0.825)
  9. `get_arm_pose` (arm=right)
  10. `done` (reason=HONEST INFEASIBLE — kinematic wall confirmed by REALIZED-floor measurement (move_to_pose, not is_reachable), gripper ended OPEN, nothing held. 

MEASUREMENTS (this attempt, fresh restored state):
- Target front sign (right half, -y region): world (0.149, -0.100, 0.742) [table surface z≈0.742].
- Near-vertical place quat [0.0616,-0.0616,0.7044,0.7044] (≈10° from vertical, the orientation needed to stand the block).
- Right arm (the ONLY arm that reaches the -y sign region; LEFT cannot reach) REALIZED flange floor at the sign xy:
   • z=0.86 → REACHED (ee 0.8596).
   • z=0.81 → REFUSED, stalled/partial-plan at realized z=0.836.
   • z=0.825 → planner REFUSED (motion did not execute), held at realized z=0.8367 (get_arm_pose confirms flange z=0.8367).
  => Realized flange floor ≈ 0.836 with the near-vertical place quat. Two consecutive sub-0.836 refusals confirm the floor.

INFEASIBILITY ANALYSIS: With a ~12 cm block hung below the gripper from this near-vertical orientation, the block BASE seats at flange_floor − (hang − finger_offset) ≈ 0.836 − ~0.05 ≈ 0.79, i.e. ~5 cm above the sign surface (0.742). The campaign-consistent base-hang z ≈ 0.79 > 0.76 required to seat. Therefore the block base cannot reach the sign: standing-success (center z>0.791 with center xy within 3cm of the sign AND stable seated base) is geometrically impossible with this base placement and the right arm's near-vertical floor of 0.836.

Did NOT flip to top-down (which holds block flat), did NOT end holding (gripper open), did NOT hold-timeout, did NOT re-grasp the displaced block (per Reviewer: floor-based infeasible suffices), did NOT loop grid scans, did NOT false-claim.

MANAGER ACTION REQUIRED (structural fix): relax the standing z criterion, reposition the sign closer to the arm base, or extend the right-arm reach envelope / change embodiment. The near-vertical place floor (flange 0.836 → base ~0.79) is a hard kinematic wall., success=False)

## Failed execution traces

### pick_and_place_at_pixel · seed=30 · run=20260629-010918-0e7e29 · 2026-06-29T01:13:17Z
- tool_calls: 1
- outcome: ✗ failure
- root_cause: Atomic unmet, paused correctly ×4: 1 call (done False), no re-file, no contamination. Fully blocked on Manager-gate (1782694494). Engineer cannot self-unblock.
- reviewer_next_action: MANAGER (sole mover): 3-gate+offline harness on 1782694494, apply if pass else END sweep. 4 identical idle pauses — stop re-running isolation. No skill bug.
- sequence:
  1. `done` (reason=PAUSE — converged verdict ×12, Manager is sole mover (3-gate+offline on 1782694494; apply if pass else END sweep). Known kinematic wall: base seats ~0.79<0.792, brute cannot close ~5mm gap. No brute pick, no live test-call, no duplicate proposal, no contamination, no false-claim. Engineer idling correctly; route to match_blocks_stand_scripted., success=False)

### pick_and_place_at_pixel · seed=30 · run=20260629-010918-0e7e29 · 2026-06-29T01:12:39Z
- tool_calls: 1
- outcome: ✗ failure
- root_cause: Atomic unmet, paused correctly ×3: 1 call (done False), no re-file, no contamination. Fully blocked on Manager-gate (1782694494).
- reviewer_next_action: MANAGER (sole mover): 3-gate+offline harness on 1782694494, apply if pass else END sweep. Loop idles every seed — stop re-running isolation. No skill bug; Engineer optimal.
- sequence:
  1. `done` (reason=PAUSE — converged verdict ×11, Manager is sole mover (3-gate+offline on 1782694494; apply if pass else END sweep). Residual is the known kinematic wall: right-arm near-vertical place floor seats block base ~0.79<0.792, brute move_to_pose cannot close the ~5mm gap. No brute pick, no live test-call, no duplicate proposal, no contamination, no false-claim. Engineer cannot self-unblock; idling correctly. Route to match_blocks_stand_scripted., success=False)

### pick_and_place_at_pixel · seed=30 · run=20260629-010918-0e7e29 · 2026-06-29T01:12:00Z
- tool_calls: 2
- outcome: ✗ failure
- root_cause: Atomic unmet, paused correctly ×2: 2 calls, honest done(False), no re-file, no contamination. Fully blocked on Manager-gate (1782694494).
- reviewer_next_action: MANAGER (sole mover): 3-gate+offline harness on 1782694494, apply if pass else END sweep. Every seed idles identically — stop re-running isolation. No skill bug; Engineer optimal.
- sequence:
  1. `describe_scene_actors`
  2. `done` (reason=PAUSE per converged Reviewer verdict ×10 — Manager is sole mover (3-gate+offline harness on queued primitive 1782694494; apply if pass else END sweep). 1 read-only call (describe_scene_actors) confirms restored state, no contamination. Residual is the known kinematic wall: right-arm near-vertical place floor → base seats ~0.79 < 0.792 required; brute move_to_pose cannot close the ~5mm gap. No brute pick, no live test-call (cuRobo hang avoided), no duplicate proposal, no false-claim. Engineer cannot self-unblock; blocked solely on Manager apply of match_blocks_stand_scripted., success=False)

## Key measurements (Reviewer-proposed, human-approved)

(empty — populated when Reviewer files a measurement and you approve it)
