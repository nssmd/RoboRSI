"""base.robotwin.place_object_in — end-to-end placement (plugin path).

v0.3 — drop-height LADDER + never-release-on-failed-move + WARM-START RESET.
The grasping arm sometimes cannot reach the container at the nominal drop_h
(cross-body IK boundary), and — proven across clean_table_bicoord seed-21
attempts 2 & 3 — the bin can be IK-FEASIBLE (probe_ik_workspace top-down at
z 0.80-0.83) yet EVERY live plan to it fails because cuRobo's motion_gen
warm-start has DRIFTED across the many prior tool calls (grasp retries etc.).
So: try a ladder of drop heights; if the WHOLE ladder fails, reset the
warm-start to near-HOME via the shared warmup_planner() helper and retry the
ladder ONCE; only open the gripper after a move that actually succeeded; if
all fail (even post-reset) return ok=False WITHOUT releasing (never fling).
"""

from __future__ import annotations

from typing import Any


def _attempt_ladder(state, arm, tx, ty, tz, ladder, quat, attempts, phase):
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_fingertip_to
    for dh in ladder:
        cand = [tx, ty, tz + max(dh, 0.01)]
        r, _ = _do_move_fingertip_to(state, {"arm": arm, "x": cand[0],
                                             "y": cand[1], "z": cand[2], "quat": quat})
        attempts.append({"step": "move_above_target", "phase": phase,
                         "drop_h": round(dh, 3), "ok": r.get("ok"),
                         "drop_xyz": cand, "ee_after": r.get("ee_after")})
        if r.get("ok"):
            return r, cand
    return None, None


def dispatch_runtime(state: Any, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_find_pixel, _do_unproject_pixel, _do_gripper, _do_is_holding, _do_move_fingertip_to
    from roborsi.embodied.sim.robotwin.gripper_geom import warmup_planner

    arm = str(args.get("arm", "")).lower()
    target = str(args.get("target", ""))
    drop_h = float(args.get("drop_height_m", 0.05))
    retreat_m = float(args.get("retreat_m", 0.15))
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"}, _snapshot(state.env))
    if not target:
        return ({"ok": False, "reason": "target name required"}, _snapshot(state.env))

    attempts: list[dict[str, Any]] = []

    pixel_res, _ = _do_find_pixel(state, {"object": target, "location": "center"})
    if not pixel_res.get("ok"):
        return ({"ok": False, "reason": f"find_pixel failed: {pixel_res.get('reason')}",
                 "attempts": attempts}, _snapshot(state.env))
    u, v = int(pixel_res["u"]), int(pixel_res["v"])
    attempts.append({"step": "find_pixel", "u": u, "v": v,
                     "confidence": pixel_res.get("confidence")})

    unp_res, _ = _do_unproject_pixel(state, {"u": u, "v": v, "camera": "head_camera"})
    if not unp_res.get("ok"):
        return ({"ok": False, "reason": f"unproject failed: {unp_res.get('reason')}",
                 "attempts": attempts}, _snapshot(state.env))
    tx, ty, tz = (float(c) for c in unp_res["xyz"])
    attempts.append({"step": "unproject", "target_xyz": [tx, ty, tz]})

    import math
    impl = state.env._impl
    base_name = "fl_base_link" if arm == "left" else "fr_base_link"
    other_name = "fr_base_link" if arm == "left" else "fl_base_link"
    art = impl.scene.get_all_articulations()[0]
    bl = next(l for l in art.get_links() if l.get_name() == base_name)
    ol = next(l for l in art.get_links() if l.get_name() == other_name)
    bp = bl.get_pose().p
    op = ol.get_pose().p
    bx, by, bz = float(bp[0]), float(bp[1]), float(bp[2])
    midline_x = 0.5 * (bx + float(op[0]))
    dx = tx - bx; dy = ty - by; dz = (tz + drop_h) - bz
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    cross = (arm == "left" and tx > midline_x + 0.05) or \
            (arm == "right" and tx < midline_x - 0.05)
    if dist > 0.75 or cross:
        return ({"ok": False, "reason": (f"target unreachable for {arm} arm "
                                          f"(dist={dist:.3f}, cross_midline={cross}). "
                                          f"Try the other arm."),
                 "attempts": attempts, "target_xyz": [tx, ty, tz]},
                _snapshot(state.env))

    quat = [0.5, -0.5, 0.5, 0.5]
    ladder = [drop_h, drop_h - 0.02, drop_h + 0.02, drop_h - 0.04, drop_h + 0.05]

    # First ladder pass from the live (possibly drifted) warm-start.
    mv_res, drop_xyz = _attempt_ladder(state, arm, tx, ty, tz, ladder, quat,
                                       attempts, "live")
    # Warm-start drift recovery: a bin that is IK-feasible (probe) can be
    # unplannable from a drifted warm-start. Reset to near-HOME and retry once.
    if mv_res is None:
        reset_ok = warmup_planner(impl, arm)
        attempts.append({"step": "warmstart_reset", "ok": reset_ok})
        mv_res, drop_xyz = _attempt_ladder(state, arm, tx, ty, tz, ladder, quat,
                                           attempts, "post_reset")
    if mv_res is None or drop_xyz is None:
        return ({"ok": False,
                 "reason": (f"could not move {arm} arm above {target} at any drop "
                            f"height in {ladder}, even after a warm-start reset; "
                            f"NOT releasing (would drop the object on the table). "
                            f"The {arm} arm likely cannot reach the container — "
                            f"hand off to the other arm or bail."),
                 "attempts": attempts}, _snapshot(state.env))

    op_res, _ = _do_gripper(state, {"arm": arm, "action": "open"})
    attempts.append({"step": "gripper_open_1", "ok": op_res.get("ok")})

    held_res, _ = _do_is_holding(state, {"arm": arm})
    attempts.append({"step": "post_release_holding_1", **held_res})
    val = float(held_res.get("gripper_val", 0.0))

    if val < 0.8:
        wiggle_up = [drop_xyz[0], drop_xyz[1], drop_xyz[2] + 0.02]
        _do_move_fingertip_to(state, {"arm": arm, "x": wiggle_up[0],
                                       "y": wiggle_up[1], "z": wiggle_up[2], "quat": quat})
        _do_gripper(state, {"arm": arm, "action": "open"})
        wiggle_dn = [drop_xyz[0], drop_xyz[1], max(drop_xyz[2] - 0.01, tz + 0.005)]
        _do_move_fingertip_to(state, {"arm": arm, "x": wiggle_dn[0],
                                       "y": wiggle_dn[1], "z": wiggle_dn[2], "quat": quat})
        _do_gripper(state, {"arm": arm, "action": "open"})
        held2, _ = _do_is_holding(state, {"arm": arm})
        attempts.append({"step": "post_release_holding_wiggle", **held2})
        val = float(held2.get("gripper_val", 0.0))

    retreat_xyz = [tx, ty, drop_xyz[2] + retreat_m]
    _do_move_fingertip_to(state, {"arm": arm, "x": retreat_xyz[0], "y": retreat_xyz[1],
                                   "z": retreat_xyz[2], "quat": quat})

    final, _ = _do_is_holding(state, {"arm": arm})
    released = (float(final.get("gripper_val", 0.0)) >= 0.6) and (not final.get("holding", False))
    return ({
        "ok": released,
        "released": released,
        "reason": ("released over " + target) if released
                  else (f"gripper did not fully open (val={final.get('gripper_val')}); "
                        f"object may have stayed pinched. Try place_object_in again "
                        f"or call gripper(arm, 'open') manually 2-3 more times."),
        "attempts": attempts,
        "target": target, "arm": arm,
        "target_xyz": [tx, ty, tz],
        "drop_xyz": drop_xyz,
    }, _snapshot(state.env))


def run(env, **_: Any):
    raise RuntimeError(
        "place_object_in runs inside the rollout tool loop; call via VLM tool dispatch."
    )
