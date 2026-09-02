"""base.robotwin.find_object_via_wrist — active-perception refinement.

Auto-discovered by robotwin_agent._dispatch via the plugin path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.config import _POINT_SYSTEM_PROMPT, DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _image_dims, _call_vlm_image, _parse_json
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_find_pixel
    from roborsi.embodied.sim.robotwin.robotwin_agent import _write_jpg, _unproject
    from envs.utils.action import Action, ArmTag

    arm = str(args.get("arm", "right")).lower()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"}, _snapshot(state.env))
    obj = args.get("object")
    if not obj:
        return ({"ok": False, "reason": "object (text description) is required"}, _snapshot(state.env))
    location = args.get("location", "the most graspable point")
    hover_h_req = float(args.get("hover_height_m", 0.20))
    # Try several heights in descending order if IK refuses; 30cm often
    # exceeds aloha-agilex reach near workspace edges.
    hover_candidates = []
    for h in (hover_h_req, 0.20, 0.15, 0.12, 0.10):
        if h not in hover_candidates and h > 0.05:
            hover_candidates.append(h)

    impl = state.env._impl

    # Step 1+2: head snapshot + coarse find_pixel + unproject.
    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera"}, obs)
    head_path = state.workdir / f"wrist_refine_head_{len(list(state.workdir.glob('wrist_refine_head_*.jpg'))):03d}.jpg"
    _write_jpg(head_path, head)
    state.last_image_path = head_path

    coarse_res, _ = _do_find_pixel(state, {"object": obj, "location": location})
    if not coarse_res.get("ok"):
        return ({"ok": False, "reason": f"coarse find_pixel failed: {coarse_res.get('reason')}"}, _snapshot(state.env))
    u_h, v_h = int(coarse_res["u"]), int(coarse_res["v"])

    coarse_xyz, info = _unproject(impl, "head_camera", u_h, v_h)
    if coarse_xyz is None:
        return ({"ok": False, "reason": f"head unproject failed: {info}"}, _snapshot(state.env))
    cx, cy, cz = float(coarse_xyz[0]), float(coarse_xyz[1]), float(coarse_xyz[2])

    # Step 3: hover the chosen wrist above coarse point with top-down quat.
    # Retry with progressively smaller heights if IK refuses.
    GRASP_QUAT = [0.5, -0.5, 0.5, 0.5]
    FINGER_OFFSET = 0.18
    hover_used = None
    for h_try in hover_candidates:
        target_z = cz + FINGER_OFFSET + h_try
        impl.plan_success = True
        ee_before = impl.robot.get_left_ee_pose() if arm == "left" else impl.robot.get_right_ee_pose()
        impl.move((ArmTag(arm), [Action(ArmTag(arm), "move",
                                        target_pose=[cx, cy, target_z, *GRASP_QUAT])]))
        ee_after = impl.robot.get_left_ee_pose() if arm == "left" else impl.robot.get_right_ee_pose()
        delta = ((float(ee_after[0]) - float(ee_before[0])) ** 2 +
                 (float(ee_after[1]) - float(ee_before[1])) ** 2 +
                 (float(ee_after[2]) - float(ee_before[2])) ** 2) ** 0.5
        if delta > 0.005:
            hover_used = h_try
            break
    if hover_used is None:
        return ({"ok": False, "reason": f"hover plan refused at all heights {hover_candidates}. "
                 f"Likely IK/collision — try different arm or move the arm out of the way first.",
                 "coarse_xyz": [cx, cy, cz]}, _snapshot(state.env))

    # Step 4: scan wrist.
    cam_name = f"{arm}_camera"
    obs = _snapshot(state.env)
    wrist_rgb = obs.images.get(cam_name)
    if wrist_rgb is None:
        return ({"ok": False, "reason": f"wrist camera '{cam_name}' not in obs",
                 "coarse_xyz": [cx, cy, cz]}, obs)
    wrist_path = state.workdir / f"wrist_refine_{arm}_{len(list(state.workdir.glob(f'wrist_refine_{arm}_*.jpg'))):03d}.jpg"
    _write_jpg(wrist_path, wrist_rgb)
    state.last_image_path = wrist_path

    # Step 5: VLM refine on wrist view (use POINT prompt directly so we can target wrist image).
    w, h = _image_dims(wrist_path)
    system = _POINT_SYSTEM_PROMPT.replace("IMG_W", str(w or 320)).replace("IMG_H", str(h or 240))
    user = (f"This is a CLOSE-UP wrist-camera view. Find the pixel coordinates of "
            f"{location} of {obj}. Be precise — this is a refined view, expected ~1mm/pixel.")
    raw = _call_vlm_image(DEFAULT_MODEL, system, user, wrist_path)
    parsed = _parse_json(raw)
    if not parsed or "u" not in parsed:
        return ({"ok": False, "reason": "wrist VLM did not return a usable point",
                 "raw": raw[:200], "coarse_xyz": [cx, cy, cz],
                 "wrist_image": str(wrist_path)}, obs)
    u_w, v_w = int(parsed["u"]), int(parsed["v"])

    # Step 6: unproject wrist pixel.
    refined_xyz, info = _unproject(impl, cam_name, u_w, v_w)
    if refined_xyz is None:
        return ({"ok": False, "reason": f"wrist unproject failed: {info}",
                 "u": u_w, "v": v_w, "coarse_xyz": [cx, cy, cz],
                 "wrist_image": str(wrist_path)}, obs)
    rx, ry, rz = float(refined_xyz[0]), float(refined_xyz[1]), float(refined_xyz[2])
    drift = ((rx - cx) ** 2 + (ry - cy) ** 2 + (rz - cz) ** 2) ** 0.5

    return ({"ok": True,
             "arm": arm,
             "u": u_w, "v": v_w,
             "xyz": [rx, ry, rz],
             "coarse_xyz": [cx, cy, cz],
             "refinement_drift_m": round(drift, 4),
             "wrist_image": str(wrist_path),
             "confidence": parsed.get("confidence"),
             "reasoning": parsed.get("reasoning"),
             "note": "Refined xyz is from a close-up wrist view — use this for grasp targeting."}, obs)


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
