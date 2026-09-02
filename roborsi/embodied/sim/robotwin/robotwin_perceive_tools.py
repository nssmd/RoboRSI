"""RoboTwin perception + measurement tools (`_do_<name>`) for the VLM loop.

Self-contained base-tool handlers (look, wrist scan, pixel grounding, grid
labeling, geometric measurements, arm-pose readout, RAG recall). Split out of
robotwin_tools to keep each file <1000 lines. Registered via robotwin_tools,
which re-exports these so `_ensure_registry` sees the full set. Sim runtime
helpers live in robotwin_agent and are imported lazily inside each handler;
`_State` is a string annotation only.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image, _parse_json
from roborsi.embodied.agent_loop.env import Observation

if TYPE_CHECKING:
    from roborsi.embodied.agent_loop.rollout import DispatchContext as _State


def _do_look(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_agent import _write_jpg
    cam = args.get("camera", "head_camera")
    obs = _snapshot(state.env)
    rgb = obs.images.get(cam)
    if rgb is None:
        return ({"ok": False, "reason": f"camera '{cam}' not available; have {list(obs.images)}"}, obs)
    path = state.workdir / f"look_{len(list(state.workdir.glob('look_*.jpg'))):03d}.jpg"
    _write_jpg(path, rgb)
    state.last_image_path = path
    return ({"ok": True, "image_path": str(path), "camera": cam, "shape": list(rgb.shape)}, obs)


def _do_move_to_pixel(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_agent import _unproject, _execute_move, _write_jpg
    arm = args.get("arm", "right")
    u = int(args.get("u", -1))
    v = int(args.get("v", -1))
    action = args.get("action", "hover")
    height = float(args.get("height_above_m", 0.05))
    cam = args.get("camera", "head_camera")
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be 'left' or 'right', got {arm!r}"}, _snapshot(state.env))
    impl = state.env._impl
    world_xyz, info = _unproject(impl, cam, u, v)
    if world_xyz is None:
        return ({"ok": False, "reason": info, "ee_xyz": None}, _snapshot(state.env))
    ok, reason = _execute_move(impl, arm, world_xyz, action, height)
    obs = _snapshot(state.env)
    # Auto-attach a fresh head_camera frame so VLM can see what changed.
    head = obs.images.get("head_camera")
    if head is not None:
        path = state.workdir / f"after_move_{len(list(state.workdir.glob('after_move_*.jpg'))):03d}.jpg"
        _write_jpg(path, head)
        # NO auto-attach: motion tool. Engineer must explicitly call view_frame
        # if it wants to see the result. (per user 2026-06-10: pull-on-demand)
    return ({"ok": ok, "reason": reason, "ee_xyz": world_xyz.tolist()}, obs)


def _do_unproject_pixel(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Expose pixel → world XYZ so the VLM can compose its own grasp."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_agent import _unproject
    cam = args.get("camera", "head_camera")
    u = int(args.get("u", -1))
    v = int(args.get("v", -1))
    obs = _snapshot(state.env)
    impl = state.env._impl
    world_xyz, info = _unproject(impl, cam, u, v)
    if world_xyz is None:
        return ({"ok": False, "reason": info}, obs)
    return ({"ok": True, "xyz": [float(x) for x in world_xyz], "camera": cam}, obs)


def _do_zoom_in(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Crop a square region around (u, v) of the latest look image and surface
    it as the next-turn image (4x upscaled). Lets the VLM see small objects."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    obs = _snapshot(state.env)
    if state.last_image_path is None or not Path(state.last_image_path).exists():
        return ({"ok": False, "reason": "no recent image; call look() first"}, obs)
    u = int(args.get("u", -1))
    v = int(args.get("v", -1))
    half = int(args.get("half_size_px", 80))
    half = max(20, min(half, 240))
    import cv2
    img = cv2.imread(str(state.last_image_path))
    if img is None:
        return ({"ok": False, "reason": "could not read last image"}, obs)
    h, w = img.shape[:2]
    if not (0 <= u < w and 0 <= v < h):
        return ({"ok": False, "reason": f"pixel ({u},{v}) out of range ({w}x{h})"}, obs)
    u0, u1 = max(0, u - half), min(w, u + half)
    v0, v1 = max(0, v - half), min(h, v + half)
    crop = img[v0:v1, u0:u1]
    if crop.size == 0:
        return ({"ok": False, "reason": "zoom crop is empty"}, obs)
    upscaled = cv2.resize(crop, (crop.shape[1] * 4, crop.shape[0] * 4), interpolation=cv2.INTER_LINEAR)
    path = state.workdir / f"zoom_{len(list(state.workdir.glob('zoom_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(path), upscaled)
    state.last_image_path = path
    return ({"ok": True, "zoom_image_path": str(path),
             "zoom_window": {"u0": u0, "u1": u1, "v0": v0, "v1": v1, "scale": 4},
             "note": "Find_pixel on this image returns ZOOMED coordinates. To map back to "
                     "the original frame: u_orig = u0 + u_zoom // 4, v_orig = v0 + v_zoom // 4. "
                     "For move_to_pixel / unproject_pixel always use ORIGINAL frame coordinates."}, obs)


def _do_label_points_grid(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Rollout Tier 3: overlay N numbered candidate points and let the VLM
    pick by INDEX (never raw coords). Two modes:

      mask_from_query=<text>  →  Grounded-SAM masks the named object first;
                                 grid is sampled INSIDE the mask only. Use
                                 this for affordance-precise selection
                                 (towel corners, button center, etc.) — the
                                 VLM only chooses among on-object candidates.

      (default, no query)     →  uniform full-image grid, legacy behavior.
                                 Useful for "set-of-mark" coarse pointing
                                 when no specific object is named yet."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera"}, obs)
    n = int(args.get("grid_n", 5))
    margin = int(args.get("margin_px", 30))
    mask_query = (args.get("mask_from_query") or "").strip()
    import cv2
    img_rgb = np.asarray(head)
    img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR) if img_rgb.dtype == np.uint8 else img_rgb
    h, w = img.shape[:2]
    overlay = img.copy()
    labels: dict[str, list[int]] = {}

    if mask_query:
        # Mask-constrained mode (Rollout Tier 3 proper).
        from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
        dets = detect(img_rgb, mask_query, top_k=2)
        if not dets:
            return ({"ok": False,
                     "reason": f"Grounded-SAM did not find '{mask_query}' to mask the grid"}, obs)
        ys_m, xs_m = np.where(dets[0].mask)
        if len(xs_m) < n:
            return ({"ok": False, "reason": f"mask too small ({len(xs_m)} px) for {n} candidates"}, obs)
        stride = max(1, len(xs_m) // n)
        picks = list(range(0, len(xs_m), stride))[:n]
        for i, p in enumerate(picks, start=1):
            x, y = int(xs_m[p]), int(ys_m[p])
            cv2.circle(overlay, (x, y), 6, (0, 165, 255), -1)
            cv2.putText(overlay, str(i), (x + 7, y - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
            labels[str(i)] = [x, y]
        path = state.workdir / f"labeled_mask_{len(list(state.workdir.glob('labeled_*.jpg'))):03d}.jpg"
        cv2.imwrite(str(path), overlay)
        state.last_image_path = path
        return ({"ok": True, "labeled_image_path": str(path), "labels": labels,
                 "mask_from": mask_query, "mask_score": round(dets[0].score, 3),
                 "note": "Numbered candidates are CONSTRAINED to the mask interior. "
                         "Pick a label index, then call unproject_pixel or "
                         "move_to_pixel with that label's (u, v)."}, obs)

    # Legacy uniform-grid mode (no mask).
    n = max(2, min(n, 9))
    idx = 1
    xs = [margin + i * (w - 2 * margin) // (n - 1) for i in range(n)]
    ys = [margin + i * (h - 2 * margin) // (n - 1) for i in range(n)]
    for y in ys:
        for x in xs:
            cv2.circle(overlay, (x, y), 8, (0, 0, 255), -1)
            cv2.putText(overlay, str(idx), (x + 10, y + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            labels[str(idx)] = [int(x), int(y)]
            idx += 1
    path = state.workdir / f"labeled_{len(list(state.workdir.glob('labeled_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(path), overlay)
    state.last_image_path = path
    return ({"ok": True, "labeled_image_path": str(path), "labels": labels,
             "note": "Uniform grid (no mask constraint). Pass mask_from_query="
                     "<obj> to constrain candidates to that object's pixels."}, obs)


def _do_get_object_bbox(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Rollout-aligned bbox grounding via Grounding-DINO. Deterministic;
    confidence is the detector score, not a VLM self-report. Returns
    (u_min, v_min, u_max, v_max) and the mask centroid (more stable than
    bbox center for non-convex / partially occluded objects)."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera"}, obs)
    obj = args.get("object", "the target")
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    dets = detect(np.asarray(head), obj, top_k=3)
    if not dets:
        return ({"ok": False, "reason": f"Grounded-SAM did not find '{obj}'"}, obs)
    top = dets[0]
    u0, v0, u1, v1 = top.bbox
    cu, cv = top.centroid
    return ({"ok": True, "bbox": [u0, v0, u1, v1], "centroid": [cu, cv],
             "width_px": u1 - u0, "height_px": v1 - v0,
             "confidence": round(top.score, 3),
             "n_alternatives": len(dets) - 1,
             "note": "Use centroid as a more stable pixel target than bbox center "
                     "for non-convex / partially-occluded objects."}, obs)


def _do_measure_distance(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Euclidean distance between two 3D points (or 2D pixels)."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    p1 = args.get("p1") or args.get("a")
    p2 = args.get("p2") or args.get("b")
    if not p1 or not p2 or len(p1) != len(p2):
        return ({"ok": False, "reason": "need p1 and p2 of equal length"}, _snapshot(state.env))
    arr1 = np.asarray(p1, dtype=np.float64)
    arr2 = np.asarray(p2, dtype=np.float64)
    return ({"ok": True, "distance": float(np.linalg.norm(arr1 - arr2)),
             "delta": (arr2 - arr1).tolist()}, _snapshot(state.env))


def _do_measure_vector(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Vector from p1 → p2, plus its length and unit direction."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    p1 = args.get("p1") or args.get("from")
    p2 = args.get("p2") or args.get("to")
    if not p1 or not p2 or len(p1) != len(p2):
        return ({"ok": False, "reason": "need p1 and p2 of equal length"}, _snapshot(state.env))
    arr1 = np.asarray(p1, dtype=np.float64)
    arr2 = np.asarray(p2, dtype=np.float64)
    vec = arr2 - arr1
    length = float(np.linalg.norm(vec))
    unit = (vec / length).tolist() if length > 1e-9 else [0.0] * len(vec)
    return ({"ok": True, "vector": vec.tolist(), "length": length, "unit": unit},
            _snapshot(state.env))


def _do_measure_relative_rotation(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Signed angle from v1 to v2 in degrees (around `axis`, default +Z)."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_agent import _wrap_pi
    v1 = np.asarray(args.get("v1") or [], dtype=np.float64)
    v2 = np.asarray(args.get("v2") or [], dtype=np.float64)
    axis = str(args.get("axis", "z")).lower()
    if v1.size != v2.size or v1.size not in (2, 3):
        return ({"ok": False, "reason": "v1, v2 must be both 2D or both 3D"}, _snapshot(state.env))
    if v1.size == 2:
        a1 = np.arctan2(v1[1], v1[0])
        a2 = np.arctan2(v2[1], v2[0])
        deg = float(np.degrees(_wrap_pi(a2 - a1)))
    else:
        n1 = v1 / max(1e-9, np.linalg.norm(v1))
        n2 = v2 / max(1e-9, np.linalg.norm(v2))
        cosang = float(np.clip(np.dot(n1, n2), -1.0, 1.0))
        ang = np.arccos(cosang)
        cross = np.cross(n1, n2)
        axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis, 2)
        sign = 1.0 if cross[axis_idx] >= 0 else -1.0
        deg = float(np.degrees(ang) * sign)
    return ({"ok": True, "angle_deg": deg, "axis": axis}, _snapshot(state.env))


def _do_rotate_vector(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Rotate a 3D (or 2D) vector by `angle_deg` around `axis`."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    vec = np.asarray(args.get("vector") or args.get("v") or [], dtype=np.float64)
    angle_deg = float(args.get("angle_deg", 0.0))
    axis = str(args.get("axis", "z")).lower()
    if vec.size not in (2, 3):
        return ({"ok": False, "reason": "vector must be 2D or 3D"}, _snapshot(state.env))
    rad = np.radians(angle_deg)
    c, s = np.cos(rad), np.sin(rad)
    if vec.size == 2:
        out = np.array([c * vec[0] - s * vec[1], s * vec[0] + c * vec[1]])
    else:
        if axis == "x":
            R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        elif axis == "y":
            R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        else:
            R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        out = R @ vec
    return ({"ok": True, "rotated": out.tolist(), "angle_deg": angle_deg, "axis": axis},
            _snapshot(state.env))


def _do_scan_wrist(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Active perception: snap a frame from a wrist camera (left_camera or
    right_camera in BiCoord/RoboTwin). Replaces last_image_path."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_agent import _write_jpg
    arm = str(args.get("arm", "right")).lower()
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"}, _snapshot(state.env))
    cam = f"{arm}_camera"
    obs = _snapshot(state.env)
    rgb = obs.images.get(cam)
    if rgb is None:
        return ({"ok": False, "reason": f"wrist camera '{cam}' not in obs.images "
                 f"(have {list(obs.images)})"}, obs)
    path = state.workdir / f"wrist_{arm}_{len(list(state.workdir.glob('wrist_*.jpg'))):03d}.jpg"
    _write_jpg(path, rgb)
    state.last_image_path = path
    return ({"ok": True, "image_path": str(path), "camera": cam,
             "shape": list(rgb.shape)}, obs)


def _do_estimate_feature_point(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Rollout Tier 3 (salient task-relevant keypoint, ReKep-style).
    Pipeline: Grounded-SAM → mask → uniform point grid CONSTRAINED TO MASK
    → VLM picks index of most affordance-relevant point. VLM never returns
    raw coordinates — only an integer index from a numbered candidate set."""
    from roborsi.embodied.agent_loop.rollout import _snapshot
    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera"}, obs)
    obj = args.get("object", "the target")
    feature = args.get("feature", "the most graspable / task-relevant point")
    n_points = int(args.get("n_grid_points", 9))

    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    dets = detect(np.asarray(head), obj, top_k=2)
    if not dets:
        return ({"ok": False, "reason": f"Grounded-SAM did not find '{obj}' for masking"}, obs)
    mask = dets[0].mask
    ys, xs = np.where(mask)
    if len(xs) < n_points:
        return ({"ok": True, "feature": feature, "u": int(xs.mean()), "v": int(ys.mean()),
                 "confidence": round(dets[0].score, 3),
                 "selection_method": "mask_centroid_fallback",
                 "note": f"mask too small ({len(xs)} px) for grid; returned centroid"}, obs)
    # Sample n_points roughly evenly inside mask via stride.
    stride = max(1, len(xs) // n_points)
    idxs = list(range(0, len(xs), stride))[:n_points]
    candidates = [(int(xs[i]), int(ys[i])) for i in idxs]

    # Render numbered overlay so VLM can pick by INDEX.
    import cv2
    img = np.asarray(head)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if img.dtype == np.uint8 else img
    overlay = img_bgr.copy()
    for i, (cu, cv) in enumerate(candidates):
        cv2.circle(overlay, (cu, cv), 5, (0, 165, 255), -1)
        cv2.putText(overlay, str(i), (cu + 6, cv - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)
    grid_path = state.workdir / f"feature_grid_{len(list(state.workdir.glob('feature_grid_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(grid_path), overlay)

    system = ("You select an INDEX from a numbered candidate set. The image shows "
              "an object's mask annotated with N orange-numbered points. Reply "
              "with a single JSON object: {\"index\": int, \"reason\": \"<one sentence>\"}.")
    user = (f"Object: {obj}. Feature wanted: {feature}. "
            f"Numbered candidates: 0..{len(candidates)-1}. "
            f"Pick the index closest to that feature.")
    raw = _call_vlm_image(DEFAULT_MODEL, system, user, grid_path)
    parsed = _parse_json(raw) or {}
    idx = int(parsed.get("index", 0))
    if not (0 <= idx < len(candidates)):
        idx = 0
    cu, cv = candidates[idx]
    return ({"ok": True, "feature": feature, "u": cu, "v": cv,
             "confidence": round(dets[0].score, 3),
             "selection_method": "mask_grid_vlm_index",
             "n_candidates": len(candidates),
             "selected_index": idx,
             "vlm_reason": str(parsed.get("reason", ""))[:160],
             "grid_image": str(grid_path),
             "note": "Grounded-SAM mask + grid + VLM picks index. VLM never "
                     "returned raw (u,v) — only chose among constrained candidates."}, obs)


def _do_get_arm_pose(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Read the named arm's current EE pose [x, y, z, qx, qy, qz, qw].

    THE oracle for "where is my hand right now". Use this when you need to:
      - Compute a target pose relative to a held object (e.g. drop block above
        the bowl HELD by right arm — the bowl's world XYZ ≈ right arm EE XYZ).
      - Verify a move_to_pose actually reached its target (compare commanded
        vs returned pose).
      - Reason about workspace: are both arms in collision distance?

    Returns world-frame EE pose. The pose is the FLANGE pose, not the
    fingertip — fingertip is ~0.18 m below flange for the standard top-down
    grasp quat.
    """
    from roborsi.embodied.agent_loop.rollout import _snapshot
    arm = args.get("arm", "right")
    if arm not in {"left", "right"}:
        return ({"ok": False, "reason": f"arm must be left/right, got {arm!r}"}, _snapshot(state.env))
    impl = state.env._impl
    pose = (impl.robot.get_left_ee_pose() if arm == "left" else impl.robot.get_right_ee_pose())
    pose = list(map(float, pose))  # serialise [x,y,z,qx,qy,qz,qw]
    # Mesh-measured flange→fingertip offset along world -Z for top-down quat.
    from roborsi.embodied.sim.robotwin.gripper_geom import ALOHA_TCP_IN_EE_LOCAL
    finger_off = float(ALOHA_TCP_IN_EE_LOCAL[0])
    return ({"ok": True, "arm": arm, "ee_pose": pose,
             "xyz": pose[:3], "quat": pose[3:],
             "fingertip_xyz_top_down": [pose[0], pose[1], pose[2] - finger_off],
             "note": (f"Use ee_pose[:3] as world XYZ of the arm flange. For top-down grasp "
                      f"the fingertip is ~{finger_off*100:.1f} cm below (measured from URDF + "
                      f"link7.STL). If this arm is currently HOLDING an "
                      "object, the object's world XYZ is approximately fingertip_xyz_top_down.")},
            _snapshot(state.env))


def _do_recall_past_success(state: _State, args: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
    """Look up past SUCCESSFUL traces for this atomic from RAG history.

    Useful when the VLM is stuck — call this to see what worked before, then
    mimic the tool sequence. Cheaper than always injecting RAG into the prompt
    (which can confuse tool_use parsing on long prompts).
    """
    from roborsi.embodied.agent_loop.rollout import _snapshot
    atomic = args.get("atomic") or args.get("task_name")
    k = int(args.get("k", 1))
    if not atomic:
        return ({"ok": False, "reason": "must pass atomic name (the skill we're trying to do)"}, _snapshot(state.env))
    try:
        from roborsi.agent.explore import successful_traces, render_trace_brief
    except ImportError as exc:
        return ({"ok": False, "reason": f"explore lib missing: {exc}"}, _snapshot(state.env))
    succs = successful_traces(atomic, limit=k)
    if not succs:
        return ({"ok": True, "atomic": atomic, "count": 0,
                 "traces": [],
                 "note": "no past successes for this atomic yet — you are the first."},
                _snapshot(state.env))
    rendered = [render_trace_brief(rec, max_calls=20) for rec in succs[:k]]
    return ({"ok": True, "atomic": atomic, "count": len(succs[:k]),
             "traces": rendered,
             "note": "These tool sequences worked before. Mimic the structure but adapt args "
                     "to the current scene (find_pixel/get_object_bbox returns will differ)."},
            _snapshot(state.env))
