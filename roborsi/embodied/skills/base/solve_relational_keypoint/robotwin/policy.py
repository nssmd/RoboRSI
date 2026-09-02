"""base.robotwin.solve_relational_keypoint — ReKep relational keypoint move.

Source: ReKep (Relational Keypoint Constraints, Huang et al. 2024). A
manipulation goal is expressed as a constraint between a keypoint on the
HELD object and a keypoint on a TARGET object — e.g. "the pouring edge of
the held bowl" should reach "the center of the right bowl". This skill
grounds both keypoints with the VLM, lifts them to 3D, and computes the
end-effector displacement that satisfies the constraint, then (optionally)
executes it on the holding arm.

Pipeline:
  1. _do_find_pixel + _do_unproject_pixel for moving_keypoint  → P_move (3D)
  2. _do_find_pixel + _do_unproject_pixel for target_keypoint → P_target (3D)
  3. goal point  = P_target + offset_xyz
     The moving keypoint is rigidly attached to the held object/gripper, so
     the world DISPLACEMENT to apply to the end-effector equals
        delta = goal - P_move
     and the target EE pose = current EE pose translated by `delta`
     (orientation unchanged — ReKep here is a pure-translation constraint;
     rotational alignment of the pour edge is left as a TODO below).
  4. dry_run (DEFAULT True): return the computed delta + target EE pose
     WITHOUT moving. Set dry_run=False to actually move the holding arm
     via move_to_pose.

Geometry is approximate (single-camera unproject, keypoint jitter); keep
dry_run on first to inspect the computed delta before committing.
"""
from __future__ import annotations

from typing import Any


def _ground_keypoint(state, object_hint: str, keypoint: str):
    """find_pixel(keypoint) → unproject → 3D world point. Returns
    (xyz | None, info_dict). info_dict carries pixel + reason on failure."""
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_find_pixel, _do_unproject_pixel
    fp_res, _ = _do_find_pixel(state, {"object": object_hint,
                                       "location": keypoint})
    if not fp_res.get("ok"):
        return None, {"stage": "ground", "keypoint": keypoint,
                      "reason": fp_res.get("reason")}
    u, v = int(fp_res["u"]), int(fp_res["v"])
    un_res, _ = _do_unproject_pixel(state, {"u": u, "v": v,
                                            "camera": "head_camera"})
    if not un_res.get("ok"):
        return None, {"stage": "unproject", "keypoint": keypoint,
                      "pixel": [u, v], "reason": un_res.get("reason")}
    return ([float(c) for c in un_res["xyz"]],
            {"keypoint": keypoint, "pixel": [u, v],
             "confidence": fp_res.get("confidence")})


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_to_pose

    arm = str(args.get("arm", "")).lower()
    moving_kp = (args.get("moving_keypoint") or "").strip()
    target_obj = (args.get("target_object") or "").strip()
    target_kp = (args.get("target_keypoint") or "").strip()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"},
                _snapshot(state.env))
    if not moving_kp:
        return ({"ok": False, "reason": "moving_keypoint required "
                 "(e.g. 'the pouring edge of the held bowl')"},
                _snapshot(state.env))
    if not target_kp:
        return ({"ok": False, "reason": "target_keypoint required "
                 "(e.g. 'the center of the right bowl')"},
                _snapshot(state.env))

    offset_xyz = args.get("offset_xyz")
    if offset_xyz is None:
        offset_xyz = [0.0, 0.0, 0.02]
    if isinstance(offset_xyz, str):
        import ast
        offset_xyz = ast.literal_eval(offset_xyz)
    offset_xyz = [float(c) for c in offset_xyz]
    # dry_run defaults TRUE — conservative: compute, don't move, until the
    # caller has inspected the geometry.
    dry_run = bool(args.get("dry_run", True))

    # 1+2. Ground both keypoints in 3D.
    p_move, info_move = _ground_keypoint(state, moving_kp, moving_kp)
    if p_move is None:
        return ({"ok": False,
                 "reason": (f"could not ground moving_keypoint "
                            f"'{moving_kp}': {info_move.get('reason')}"),
                 **info_move}, _snapshot(state.env))
    # The VLM grounds best on a concrete noun phrase; pass target_object as
    # the object hint when given so find_pixel anchors on the right item.
    p_target, info_target = _ground_keypoint(
        state, target_obj or target_kp, target_kp)
    if p_target is None:
        return ({"ok": False,
                 "reason": (f"could not ground target_keypoint "
                            f"'{target_kp}': {info_target.get('reason')}"),
                 "moving_keypoint_xyz": p_move,
                 **info_target}, _snapshot(state.env))

    # 3. Goal = target keypoint + offset. Delta = goal - moving keypoint.
    # The moving keypoint is rigidly attached to the held object, so the
    # EE must translate by the same delta to bring the keypoint onto goal.
    goal = [p_target[i] + offset_xyz[i] for i in range(3)]
    delta = [goal[i] - p_move[i] for i in range(3)]

    impl = state.env._impl
    ee = list(impl.robot.get_left_ee_pose() if arm == "left"
              else impl.robot.get_right_ee_pose())
    ee_xyz = [float(ee[0]), float(ee[1]), float(ee[2])]
    ee_quat = [float(ee[3]), float(ee[4]), float(ee[5]), float(ee[6])]
    target_ee_xyz = [ee_xyz[i] + delta[i] for i in range(3)]
    # TODO(ReKep rotation): a full ReKep constraint can also demand the
    # moving keypoint's surface normal align with the target (e.g. tilt the
    # pour edge DOWN over the bowl). Here we translate only and keep the
    # current EE orientation — pour tilt should be a separate
    # rotate-then-pour primitive. Revisit if pour-alignment needs it.

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "moving_keypoint": moving_kp,
        "moving_keypoint_xyz": p_move,
        "moving_keypoint_pixel": info_move.get("pixel"),
        "target_keypoint": target_kp,
        "target_keypoint_xyz": p_target,
        "target_keypoint_pixel": info_target.get("pixel"),
        "offset_xyz": offset_xyz,
        "goal_xyz": goal,
        "delta_xyz": [round(d, 4) for d in delta],
        "ee_xyz_before": ee_xyz,
        "target_ee_pose": [*target_ee_xyz, *ee_quat],
        "source": "ReKep: relational keypoint constraint (translation)",
    }

    if dry_run:
        result["note"] = ("dry_run=True — computed target_ee_pose only, NO "
                          "motion. Inspect delta_xyz, then call again with "
                          "dry_run=False (or feed target_ee_pose to move_to_pose).")
        return (result, _snapshot(state.env))

    # 4. Execute: translate the holding arm by delta (orientation kept).
    move_res, obs = _do_move_to_pose(state, {
        "arm": arm, "x": target_ee_xyz[0], "y": target_ee_xyz[1],
        "z": target_ee_xyz[2], "quat": ee_quat})
    result["ok"] = bool(move_res.get("ok"))
    result["move_executed"] = move_res.get("ok")
    result["ee_after"] = move_res.get("ee_after")
    result["move_note"] = move_res.get("note")
    if not move_res.get("ok"):
        result["reason"] = (f"move to target_ee_pose refused: "
                            f"{move_res.get('note')}")
    return (result, obs)


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
