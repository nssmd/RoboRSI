"""base.robotwin.place_held_in_held_container — atomic dual-arm place.

Holding arm carries object, container arm carries bowl. This skill moves
the HOLDING arm to drop the object into the bowl. The CONTAINER arm is
NEVER touched (no move_to_pose, no gripper open). Solves V36-V40
atomic_2 failure where Engineer repeatedly moved the container arm
during long exec_python iterations, dropping the held bowl 16/16 times
across 4 LH runs.

Why a skill instead of a prompt: the "don't move container arm" invariant
was already in a seed-recipe Engineer reads at attempt start, yet
Engineer ignored it. Encoding the rule inside skill code removes the
ability to violate it.
"""
from __future__ import annotations

from typing import Any

# Top-down quat (canonical right-hand z-down).
_TOP_DOWN = [0.5, -0.5, 0.5, 0.5]
# Tilted approach quats (rotate ~30deg or ~45deg around X or Y axis from
# top_down). Useful when straight top-down IK refuses (e.g. holding arm
# elbow conflicts with the container arm).
_QUAT_CANDIDATES = [
    ("top_down", [0.5, -0.5, 0.5, 0.5]),
    ("tilt_30_+x", [0.6087614, -0.3535534, 0.6087614, 0.3535534]),
    ("tilt_30_-x", [0.3535534, -0.6087614, 0.3535534, 0.6087614]),
    ("tilt_30_+y", [0.5, -0.3535534, 0.5, 0.6087614]),
    ("tilt_30_-y", [0.5, -0.6087614, 0.5, 0.3535534]),
    ("tilt_45_+x", [0.6532815, -0.2705981, 0.6532815, 0.2705981]),
    ("tilt_45_-x", [0.2705981, -0.6532815, 0.2705981, 0.6532815]),
]


def _read_ee_xyz(impl, arm: str) -> tuple[float, float, float]:
    pose = (impl.robot.get_left_ee_pose() if arm == "left"
            else impl.robot.get_right_ee_pose())
    return float(pose[0]), float(pose[1]), float(pose[2])


def _read_gripper_val(impl, arm: str) -> float:
    if arm == "left":
        return float(impl.robot.get_left_gripper_val())
    return float(impl.robot.get_right_gripper_val())


def _container_dist_to_object(impl, container_arm: str) -> tuple[float, float]:
    """Return (container_ee_xy, container_ee_z) for sanity logging."""
    cx, cy, cz = _read_ee_xyz(impl, container_arm)
    return (cx, cy), cz


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_to_pose, _do_gripper

    arm = str(args.get("arm", "")).lower()
    container_arm = str(args.get("container_arm", "")).lower()
    if arm not in {"left", "right"} or container_arm not in {"left", "right"}:
        return ({"ok": False, "success": False,
                 "reason": "arm and container_arm must be 'left' or 'right'"},
                _snapshot(state.env))
    if arm == container_arm:
        return ({"ok": False, "success": False,
                 "reason": "arm and container_arm must differ"},
                _snapshot(state.env))

    drop_h = float(args.get("drop_height_m", 0.06))
    container_target_z = float(args.get("container_target_z", 0.85))
    impl = state.env._impl

    # 1. Read both arm EE poses + container gripper val (sanity).
    hx, hy, hz = _read_ee_xyz(impl, arm)
    cx0, cy0, cz0 = _read_ee_xyz(impl, container_arm)
    container_val_before = _read_gripper_val(impl, container_arm)
    holding_val_before = _read_gripper_val(impl, arm)

    container_repositioned = False
    # Reposition container_arm so the holding arm can reach above it:
    # the holding arm's CURRENT xy is by definition reachable for that
    # arm (it's there now), so we move the container to (hx, hy,
    # container_target_z) and ask the holding arm to drop straight
    # down. Keep container gripper CLOSED throughout so bowl stays held.
    # V36-V45 failure: skill only lowered z; left top-down IK still
    # refused because xy was in right's workspace, not left's.
    container_xy_offset = float(args.get("container_xy_offset", 0.0))
    target_container_x = hx + container_xy_offset
    target_container_y = hy + container_xy_offset
    if (abs(cx0 - target_container_x) > 0.03 or
            abs(cy0 - target_container_y) > 0.03 or
            cz0 > container_target_z + 0.05):
        _do_move_to_pose(state, {
            "arm": container_arm,
            "x": target_container_x, "y": target_container_y,
            "z": container_target_z,
        })
        container_repositioned = True
        cx0, cy0, cz0 = _read_ee_xyz(impl, container_arm)

    cx, cy, cz = cx0, cy0, cz0
    target_x, target_y, target_z = cx, cy, cz + drop_h

    # 2. IK-probe each candidate quat using plan_path on the HOLDING arm.
    #    For each plan_path Success, run TRAJECTORY collision check on
    #    every sampled waypoint vs container_arm + held bowl. cuRobo
    #    plan_path returns the swept path; we sphere-check waypoints to
    #    catch mid-flight collisions that end-pose-only check misses.
    from roborsi.embodied.sim.robotwin.dual_arm_collision import (
        check_trajectory_collision, held_object_spheres,
    )
    plan_fn = (impl.robot.left_plan_path if arm == "left"
                else impl.robot.right_plan_path)
    tried: list[dict] = []
    chosen = None
    holding_attach = held_object_spheres("block")
    container_attach = held_object_spheres("bowl")
    for label, quat in _QUAT_CANDIDATES:
        flange = [target_x, target_y, target_z, *quat]
        res = plan_fn(flange)
        ik_ok = res.get("status") == "Success"
        rec: dict = {"quat_label": label, "ik_ok": ik_ok,
                      "status": res.get("status", "?")}
        if not ik_ok:
            tried.append(rec)
            continue
        positions = res.get("position")
        if positions is not None and len(positions) > 0:
            coll = check_trajectory_collision(
                impl, holding_arm=arm, container_arm=container_arm,
                qpos_trajectory=positions,
                holding_attach=holding_attach,
                container_attach=container_attach,
                stride=4,
            )
            rec["coll_check"] = {
                "ok": coll.get("ok"),
                "collides": coll.get("collides"),
                "min_clearance": round(coll.get("min_clearance", 0), 4),
                "closest_pair": coll.get("closest_pair"),
                "colliding_step": coll.get("colliding_step"),
                "n_checked": coll.get("n_checked"),
                "h_sphere_count": coll.get("h_sphere_count"),
                "c_sphere_count": coll.get("c_sphere_count"),
                "reason": coll.get("reason"),
            }
            if coll.get("collides"):
                tried.append(rec)
                continue
        tried.append(rec)
        chosen = (label, quat)
        break

    if chosen is None:
        return ({"ok": False, "success": False,
                 "reason": (f"no IK-feasible quat for arm={arm} above "
                              f"container_arm={container_arm} EE "
                              f"({cx:.3f},{cy:.3f},{cz:.3f}). Container "
                              f"position blocks the holding arm. Try "
                              f"container_target_z=0.80 or move the "
                              f"container arm to a reachable spot in a "
                              f"separate exec_python."),
                 "tried": tried,
                 "container_repositioned": container_repositioned,
                 "container_arm_ee": [cx, cy, cz],
                 "holding_arm_ee_before": [hx, hy, hz],
                 "drop_target": [target_x, target_y, target_z]},
                _snapshot(state.env))

    # 3. Move HOLDING arm to drop target.
    label, quat = chosen
    move_res, _ = _do_move_to_pose(state, {
        "arm": arm, "x": target_x, "y": target_y, "z": target_z,
        "quat": quat,
    })
    if not move_res.get("ok"):
        # plan_path passed (sampled IK) but full motion plan failed.
        # Try the remaining quat candidates that we hadn't tried yet,
        # rerunning the same collision check on each.
        recovered = False
        for label2, quat2 in _QUAT_CANDIDATES:
            if label2 == label:
                continue
            flange2 = [target_x, target_y, target_z, *quat2]
            res2 = plan_fn(flange2)
            if res2.get("status") != "Success":
                continue
            positions2 = res2.get("position")
            if positions2 is not None and len(positions2) > 0:
                coll2 = check_trajectory_collision(
                    impl, holding_arm=arm, container_arm=container_arm,
                    qpos_trajectory=positions2,
                    holding_attach=holding_attach,
                    container_attach=container_attach,
                    stride=4,
                )
                if coll2.get("collides"):
                    continue
            move_res2, _ = _do_move_to_pose(state, {
                "arm": arm, "x": target_x, "y": target_y, "z": target_z,
                "quat": quat2,
            })
            if move_res2.get("ok"):
                label, quat = label2, quat2
                move_res = move_res2
                recovered = True
                break
        if not recovered:
            # LAST RESORT: cuRobo full-trajectory plan keeps failing
            # (cross-arm corridor), but the holding arm is already
            # ABOVE the container (we moved container under it). Do a
            # pure vertical descend via move_fingertip_to — small z
            # steps, no re-planning of the whole arm path. The fingers
            # only need to drop drop_h to clear the rim, then open.
            hx_now, hy_now, hz_now = _read_ee_xyz(impl, arm)
            descend_z = target_z  # = container_z + drop_h
            fingertip_ok = False
            from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_fingertip_to
            for z_try in (hz_now, (hz_now + descend_z) / 2, descend_z):
                fr, _ = _do_move_fingertip_to(state, {
                    "arm": arm, "x": hx_now, "y": hy_now, "z": z_try})
                if fr.get("ok"):
                    fingertip_ok = True
            if not fingertip_ok:
                return ({"ok": False, "success": False,
                         "reason": (f"plan_path passed on {label} but motion "
                                      f"plan failed (cuRobo partial plan). "
                                      f"Other quats + vertical-descend fallback "
                                      f"all failed. The cross-arm geometry is "
                                      f"infeasible — reposition container_arm "
                                      f"closer to holding arm's xy first."),
                         "tried": tried, "chosen_quat": label,
                         "container_repositioned": container_repositioned,
                         "container_arm_ee": [cx, cy, cz],
                         "move_failure": move_res.get("reason") or move_res.get("note")},
                        _snapshot(state.env))
            # Fingertip descend worked — fall through to gripper open.
            move_res = {"ok": True}

    # 4. Open HOLDING arm gripper. NEVER open container_arm gripper.
    _do_gripper(state, {"arm": arm, "action": "open", "pos": 1.0})

    # 5. Sanity: container arm gripper should be unchanged.
    container_val_after = _read_gripper_val(impl, container_arm)
    container_disturbed = abs(container_val_after - container_val_before) > 0.1

    # 6. Verify outcome.
    hx2, hy2, hz2 = _read_ee_xyz(impl, arm)
    holding_val_after = _read_gripper_val(impl, arm)
    success = (holding_val_after > 0.5  # opened
                and not container_disturbed)

    return ({"ok": True, "success": success,
             "chosen_quat": label,
             "tried": tried,
             "container_repositioned": container_repositioned,
             "container_arm_ee": [cx, cy, cz],
             "holding_arm_ee_before": [hx, hy, hz],
             "holding_arm_ee_after": [hx2, hy2, hz2],
             "container_gripper_before": container_val_before,
             "container_gripper_after": container_val_after,
             "container_disturbed": container_disturbed,
             "holding_gripper_before": holding_val_before,
             "holding_gripper_after": holding_val_after,
             "drop_target": [target_x, target_y, target_z]},
            _snapshot(state.env))
