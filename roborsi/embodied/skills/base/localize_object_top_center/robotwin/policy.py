"""base.robotwin.localize_object_top_center — Rollout coarse→fine bundled.

Single-call high-precision XYZ of a named object's top center. Composes:
  1. label_points_grid(mask_from_query=object, grid_n=N)  ← existing skill
  2. sub-VLM call to pick the most-central-on-top label
  3. unproject_pixel of that label
  4. refine z to top-band average (true top face, not pixel z)
"""
from __future__ import annotations

import os
import re
from typing import Any

import numpy as np


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_label_points_grid, _do_unproject_pixel
    obj = (args.get("object") or "").strip()
    if not obj:
        return ({"ok": False, "reason": "object (noun phrase) required"},
                _snapshot(state.env))
    grid_n = int(args.get("grid_n", 5))
    top_band_m = float(args.get("top_band_m", 0.005))

    # Step 1: overlay numbered candidates inside the object's SAM mask.
    grid_res, _obs = _do_label_points_grid(state, {
        "mask_from_query": obj, "grid_n": grid_n ** 2,
    })
    if not grid_res.get("ok"):
        return (grid_res, _snapshot(state.env))
    labels: dict[str, list[int]] = grid_res.get("labels", {})
    labeled_image_path = grid_res.get("labeled_image_path")
    if not labels or not labeled_image_path:
        return ({"ok": False, "reason": "label_points_grid returned no candidates"},
                _snapshot(state.env))

    # Step 2: sub-VLM picks the most semantically-central label.
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", DEFAULT_MODEL)
    system = (
        f"You see a head-camera RGB image of a tabletop. The named object "
        f"'{obj}' has been masked and {len(labels)} numbered orange dots "
        "(1..N) lie INSIDE that mask. Your job: pick the SINGLE numbered dot "
        "that lies closest to the GEOMETRIC CENTER of the object's TOP face "
        "(not edges, corners, or sides). Reply with ONLY the integer index."
    )
    user = f"Which numbered dot is the center of the {obj}'s top face? Reply with one integer."
    from pathlib import Path
    raw = _call_vlm_image(model, system, user, Path(labeled_image_path))
    m = re.search(r"\b(\d+)\b", raw)
    if not m:
        return ({"ok": False, "reason": f"sub-VLM did not return a numeric index: {raw[:120]!r}",
                 "candidates": labels, "labeled_image_path": labeled_image_path},
                _snapshot(state.env))
    chosen = int(m.group(1))
    if str(chosen) not in labels:
        # Fallback: clamp to nearest valid label
        valid = sorted(int(k) for k in labels.keys())
        chosen = min(valid, key=lambda i: abs(i - chosen))
    u, v = labels[str(chosen)]

    # Step 3: depth-unproject that pixel.
    unp, _ = _do_unproject_pixel(state, {"u": u, "v": v})
    if not unp.get("ok"):
        return ({"ok": False, "reason": f"unproject at chosen pixel failed: {unp.get('reason')}",
                 "chosen_label": chosen, "candidates": labels,
                 "labeled_image_path": labeled_image_path},
                _snapshot(state.env))
    px, py, pz = unp["xyz"]

    # Step 4: z refinement.
    # MULTI-VIEW FUSION IS OPT-IN (default False): empirical probe shows
    # GroundingDINO false-positives in non-head cameras (e.g. detects the
    # red robot base as "coloured block") pollute the fused cloud,
    # turning a 16mm single-view error into a 538mm fused error.
    # Only enable multi_view=True when you have a clean obj label that
    # all cameras can detect reliably (e.g. unique color + size).
    multi_view = bool(args.get("multi_view", False))
    cameras_used: list[str] = []
    if multi_view:
        refined = _refine_xyz_multiview(state, obj, top_band_m)
        if refined is not None:
            mx, my, mz, n_top, cameras_used = refined
            px, py, pz = mx, my, mz
    if not cameras_used:
        refined_z, n_top = _refine_top_z(state, obj, top_band_m, pz)
        if refined_z is not None:
            pz = refined_z
        cameras_used = ["head_camera"]

    return ({"ok": True,
             "xyz": [float(px), float(py), float(pz)],
             "chosen_label": chosen,
             "candidates": labels,
             "labeled_image_path": labeled_image_path,
             "z_refined_from_top_band_n": n_top,
             "cameras_fused": cameras_used,
             "note": ("Coarse→fine + multi-view fusion. chosen_label = the dot "
                      "the sub-VLM judged closest to the object's top-face "
                      "center on head_camera; XYZ is the top-band centroid of "
                      "the fused SAM mask point cloud across all listed cameras.")},
            _snapshot(state.env))


_CAMERA_CANDIDATES = ("head_camera", "front_camera", "left_camera", "right_camera",
                       "observer_camera")


def _refine_xyz_multiview(state, obj: str, top_band_m: float
                          ) -> tuple[float, float, float, int, list[str]] | None:
    """Multi-view SAM + fuse top-band cloud, return (x, y, z, n_top, cams)."""
    impl = state.env._impl
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    impl._update_render(); impl.cameras.update_picture()
    rgb_all = impl.cameras.get_rgb()
    depth_all = impl.cameras.get_depth()
    cfg_all = impl.cameras.get_config()
    fused_world: list[np.ndarray] = []
    used: list[str] = []
    for cam in _CAMERA_CANDIDATES:
        if cam not in rgb_all or cam not in depth_all or cam not in cfg_all:
            continue
        world = _camera_object_cloud(rgb_all[cam], depth_all[cam], cfg_all[cam], obj)
        if world is None or len(world) < 10:
            continue
        fused_world.append(world)
        used.append(cam)
    if not fused_world:
        return None
    cloud = np.concatenate(fused_world, axis=0)
    z_max = float(cloud[:, 2].max())
    band = cloud[cloud[:, 2] >= z_max - top_band_m]
    if len(band) < 3:
        return None
    cx = float(np.median(band[:, 0]))
    cy = float(np.median(band[:, 1]))
    cz = float(band[:, 2].mean())
    return cx, cy, cz, int(len(band)), used


def _camera_object_cloud(rgb_dict, depth_dict, cfg, obj: str) -> np.ndarray | None:
    """SAM-detect obj in one camera, unproject the mask to world XYZ."""
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    rgb = rgb_dict["rgb"]
    depth = depth_dict["depth"]
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
               if rgb.max() <= 1 else rgb.astype(np.uint8))
    dets = detect(rgb, obj, top_k=2)
    if not dets:
        return None
    K = np.asarray(cfg["intrinsic_cv"])
    extr = np.asarray(cfg["extrinsic_cv"])
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3] = extr; extr = ext_h
    cam2world = np.linalg.inv(extr)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    mask = dets[0].mask
    ys, xs = np.where(mask)
    z_cam = depth[ys, xs].astype(np.float64) / 1000.0
    valid = z_cam > 0
    if not valid.any():
        return None
    ys, xs, z_cam = ys[valid], xs[valid], z_cam[valid]
    x_cam = (xs - cx) * z_cam / fx
    y_cam = (ys - cy) * z_cam / fy
    cloud = np.stack([x_cam, y_cam, z_cam, np.ones_like(z_cam)], axis=1)
    return (cam2world @ cloud.T).T[:, :3]


def _refine_top_z(state, obj: str, top_band_m: float, fallback_z: float
                  ) -> tuple[float | None, int]:
    """Run the same SAM detect, unproject the FULL mask, take the top-Z band
    (within top_band_m of the highest z), return its mean z. None if no
    valid points."""
    impl = state.env._impl
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    impl._update_render(); impl.cameras.update_picture()
    rgb_dict = impl.cameras.get_rgb().get("head_camera")
    depth_dict = impl.cameras.get_depth().get("head_camera")
    cfg = impl.cameras.get_config().get("head_camera")
    if rgb_dict is None or depth_dict is None or cfg is None:
        return None, 0
    rgb = rgb_dict["rgb"]
    depth = depth_dict["depth"]
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
               if rgb.max() <= 1 else rgb.astype(np.uint8))
    dets = detect(rgb, obj, top_k=2)
    if not dets:
        return None, 0
    K = np.asarray(cfg["intrinsic_cv"])
    extr = np.asarray(cfg["extrinsic_cv"])
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3] = extr; extr = ext_h
    cam2world = np.linalg.inv(extr)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    mask = dets[0].mask
    ys, xs = np.where(mask)
    z_cam = depth[ys, xs].astype(np.float64) / 1000.0
    valid = z_cam > 0
    if not valid.any():
        return None, 0
    ys, xs, z_cam = ys[valid], xs[valid], z_cam[valid]
    x_cam = (xs - cx) * z_cam / fx
    y_cam = (ys - cy) * z_cam / fy
    cloud = np.stack([x_cam, y_cam, z_cam, np.ones_like(z_cam)], axis=1)
    world = (cam2world @ cloud.T).T[:, :3]
    z_max = float(world[:, 2].max())
    band = world[world[:, 2] >= z_max - top_band_m]
    if len(band) < 3:
        return None, 0
    return float(band[:, 2].mean()), int(len(band))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
