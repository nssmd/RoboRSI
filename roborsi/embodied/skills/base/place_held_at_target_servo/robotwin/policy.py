"""base.robotwin.place_held_at_target_servo — closed-loop visual-servo place.

Open-loop placement (``place_object_in`` / ``pick_and_place_at_pixel``) lands the
held object ~4 cm from the target — fine for "drop in a bin", but it FAILS tasks
that need tight alignment (e.g. match_blocks: each block must sit within 3 cm xy
of its matching sign, 3/3). cuRobo's place is open-loop, so the residual grasp
offset + plan undershoot is never corrected.

This closes the loop in IMAGE space, exactly as ``descend_tcp_to_z`` closes it in
z: while STILL HOLDING the object at a hover, repeatedly

  1. ground the HELD object and the TARGET with Grounded-SAM (find_pixel),
  2. unproject both centroids to world xy via the depth camera,
  3. nudge the arm by the residual (held → target),

until the held object is within ``tol_m`` of the target, THEN (optionally)
descend straight down holding the aligned xy and release.

Perception, not ground-truth: the world xy of object/target come from
find_pixel + depth unproject (the SAME Tier-2 grounding the VLM uses), never the
task's GT poses. No set_pose / teleport / attach. Call AFTER the object is
grasped and lifted to a hover, BEFORE release.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _quat_wxyz_to_R(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = (w * w + x * x + y * y + z * z) ** 0.5
    if n < 1e-9:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])


def _tcp_now(impl, arm: str) -> np.ndarray:
    """Current fingertip TCP world position (reads the robot's own EE pose)."""
    from roborsi.embodied.sim.robotwin.gripper_geom import tcp_from_flange
    ee = (impl.robot.get_left_ee_pose() if arm == "left"
          else impl.robot.get_right_ee_pose())
    flange = np.array([float(ee[0]), float(ee[1]), float(ee[2])])
    R = _quat_wxyz_to_R(ee[3:7])
    return tcp_from_flange(impl, flange, R, arm)


def _world_xy(state, impl, obj_label: str, camera: str):
    """Ground ``obj_label`` and unproject its centroid to world xy.
    Returns (np.array([x, y]) | None, reason)."""
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_find_pixel
    from roborsi.embodied.sim.robotwin.robotwin_agent import _unproject
    fb, _obs = _do_find_pixel(state, {"object": obj_label})
    if not fb.get("ok"):
        return None, fb.get("reason", f"could not ground '{obj_label}'")
    w, st = _unproject(impl, camera, int(fb["u"]), int(fb["v"]))
    if w is None:
        return None, f"unproject '{obj_label}' failed: {st}"
    return np.array([float(w[0]), float(w[1])]), "ok"


def dispatch_runtime(state: Any, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_move_fingertip_to, _do_gripper
    arm = str(args.get("arm", "")).lower()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"},
                _snapshot(state.env))
    held = args.get("held_object") or args.get("object")
    target = args.get("target") or args.get("target_object")
    if not held or not target:
        return ({"ok": False,
                 "reason": "held_object and target (concrete noun phrases for "
                           "Grounded-SAM, e.g. 'red cube' / 'red sign') required"},
                _snapshot(state.env))

    impl = state.env._impl
    cam = str(args.get("camera", "head_camera"))
    tol = float(args.get("tol_m", 0.02))
    max_iters = int(args.get("max_iters", 5))
    max_step = float(args.get("max_step_m", 0.04))
    release = bool(args.get("release", True))
    descend_m = float(args.get("descend_m", 0.04))
    quat = args.get("quat") or [0.5, -0.5, 0.5, 0.5]

    tcp0 = _tcp_now(impl, arm)
    hover_z = float(args.get("hover_z", tcp0[2]))
    obs = _snapshot(state.env)
    hist: list[float] = []

    for i in range(max_iters):
        bxy, rb = _world_xy(state, impl, held, cam)
        if bxy is None:
            return ({"ok": False, "success": False, "arm": arm,
                     "iters": i, "err_history": hist,
                     "reason": f"cannot locate held object '{held}': {rb} "
                               "(the gripper may occlude it — try a more specific "
                               "label, look() to refresh, or a side camera)"}, obs)
        txy, rt = _world_xy(state, impl, target, cam)
        if txy is None:
            return ({"ok": False, "success": False, "arm": arm,
                     "iters": i, "err_history": hist,
                     "reason": f"cannot locate target '{target}': {rt}"}, obs)
        err = txy - bxy
        en = float(np.linalg.norm(err))
        hist.append(round(en, 4))
        if en <= tol:
            res = {"ok": True, "success": True, "aligned": True, "arm": arm,
                   "final_err_m": round(en, 4), "iters": i + 1,
                   "err_history": hist}
            if release:
                tcp = _tcp_now(impl, arm)
                _do_move_fingertip_to(state, {
                    "arm": arm, "x": float(tcp[0]), "y": float(tcp[1]),
                    "z": float(hover_z - descend_m), "quat": quat})
                _r, obs = _do_gripper(state, {"arm": arm, "action": "open"})
                res["released"] = True
            res["reason"] = (f"held object within {en * 100:.1f}cm of target "
                             f"(<= {tol * 100:.0f}cm tol)")
            return (res, obs)
        # Nudge the arm by the residual so the held object moves toward target.
        # Clamp the step magnitude so a single bad grounding can't fling the arm.
        step = err if en <= max_step else err * (max_step / en)
        tcp = _tcp_now(impl, arm)
        _res, obs = _do_move_fingertip_to(state, {
            "arm": arm, "x": float(tcp[0] + step[0]),
            "y": float(tcp[1] + step[1]), "z": float(hover_z), "quat": quat})

    bxy, _ = _world_xy(state, impl, held, cam)
    txy, _ = _world_xy(state, impl, target, cam)
    en = (float(np.linalg.norm(txy - bxy))
          if (bxy is not None and txy is not None) else None)
    return ({"ok": False, "success": False, "aligned": False, "arm": arm,
             "final_err_m": (round(en, 4) if en is not None else None),
             "iters": max_iters, "err_history": hist,
             "reason": (f"did not converge within {max_iters} iters "
                        f"(last err {hist[-1] * 100:.1f}cm > {tol * 100:.0f}cm tol)"
                        if hist else "no measurement obtained")}, obs)


def run(env=None, **_: Any):
    raise RuntimeError(
        "place_held_at_target_servo runs inside the rollout tool loop; "
        "call via VLM tool dispatch.")
