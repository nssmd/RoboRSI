"""roborsi.embodied.sim.robotwin.graspnet_infer — GraspNet-baseline inference.

Wraps GraspNet-1Billion baseline so `_do_get_grasp_pose` can return real
6-DoF grasp poses ranked by graspability score, instead of a hardcoded
top-down pose.

Setup:
  git clone https://github.com/graspnet/graspnet-baseline "$GRASPNET_REPO"
  pip install --no-build-isolation "$GRASPNET_REPO/pointnet2"
  pip install --no-build-isolation "$GRASPNET_REPO/knn"
  pip install graspnetAPI open3d
  gdown 'https://drive.google.com/uc?id=1hd0G8LN6tRpi4742XOTEisbTXNZ-1jmk' -O ~/.roborsi/models/graspnet/checkpoint-rs.tar
  export GRASPNET_CKPT=~/.roborsi/models/graspnet/checkpoint-rs.tar
  export GRASPNET_REPO="$HOME/graspnet-baseline"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np


_NET_CACHE: dict[str, Any] = {}


def _ensure_paths() -> str:
    """Add graspnet-baseline to sys.path. Returns repo root."""
    repo = os.environ.get(
        "GRASPNET_REPO",
        str(Path.home() / "graspnet-baseline"),
    )
    if not os.path.isdir(repo):
        raise RuntimeError(f"GRASPNET_REPO not found: {repo}")
    for sub in ("", "models", "utils", "dataset"):
        p = os.path.join(repo, sub) if sub else repo
        if p not in sys.path:
            sys.path.insert(0, p)
    return repo


def _load_net(checkpoint_path: str | None = None):
    """Lazy-load GraspNet model (singleton)."""
    ckpt = checkpoint_path or os.environ.get(
        "GRASPNET_CKPT", os.path.expanduser("~/.roborsi/models/graspnet/checkpoint-rs.tar"))
    if ckpt in _NET_CACHE:
        return _NET_CACHE[ckpt]
    if not os.path.isfile(ckpt):
        raise RuntimeError(f"GraspNet checkpoint not found at {ckpt}; "
                           f"download via gdown then set GRASPNET_CKPT.")
    _ensure_paths()
    import torch
    from graspnet import GraspNet
    net = GraspNet(input_feature_dim=0, num_view=300, num_angle=12, num_depth=4,
                   cylinder_radius=0.05, hmin=-0.02,
                   hmax_list=[0.01, 0.02, 0.03, 0.04], is_training=False)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    net.to(device)
    snapshot = torch.load(ckpt, map_location=device, weights_only=False)
    net.load_state_dict(snapshot["model_state_dict"])
    net.eval()
    _NET_CACHE[ckpt] = (net, device)
    return _NET_CACHE[ckpt]


def predict_grasps_around_pixel(
    impl, camera_name: str, u: int, v: int,
    half_window_px: int = 60,
    num_point: int = 20000,
    top_k: int = 5,
    z_min: float | None = None,
    z_max: float | None = None,
    checkpoint_path: str | None = None,
) -> list[dict[str, Any]]:
    """Run GraspNet around a target pixel and return top-K 6-DoF grasps.

    Args:
        impl: RoboTwin sim impl (so we can read depth + intrinsics)
        camera_name: e.g. 'head_camera'
        u, v: pixel of the object centroid
        half_window_px: workspace mask window around (u, v)
        num_point: point cloud sampling count
        top_k: how many top grasps to return
        z_min, z_max: WORLD-frame Z bounds (meters). Filter the point cloud to
            only points within [z_min, z_max] in world frame BEFORE running
            GraspNet. CRUCIAL for cube-in-bowl: pass z_min=cube_z-0.01,
            z_max=cube_z+0.05 so GraspNet sees ONLY cube points, not bowl walls.

    Returns:
        list of dicts with keys: score, pose [x,y,z,qx,qy,qz,qw],
        translation, rotation_matrix.
    """
    _ensure_paths()
    import torch
    from graspnetAPI import GraspGroup
    from utils.collision_detector import ModelFreeCollisionDetector  # noqa: F401
    from data_utils import CameraInfo, create_point_cloud_from_depth_image
    from graspnet import pred_decode

    impl._update_render()
    impl.cameras.update_picture()
    config = impl.cameras.get_config().get(camera_name)
    depth = impl.cameras.get_depth().get(camera_name, {}).get("depth")
    if config is None or depth is None:
        raise RuntimeError(f"camera '{camera_name}' missing config/depth")
    h, w = depth.shape
    if not (0 <= u < w and 0 <= v < h):
        raise ValueError(f"pixel ({u},{v}) out of {w}x{h}")

    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    cam_info = CameraInfo(float(w), float(h),
                          float(K[0, 0]), float(K[1, 1]),
                          float(K[0, 2]), float(K[1, 2]),
                          1000.0)  # scale: depth in mm, divided by scale → meters

    cloud_full = create_point_cloud_from_depth_image(depth, cam_info, organized=True)

    # Workspace mask: 2*half_window_px around (u, v) AND positive depth.
    wmask = np.zeros_like(depth, dtype=bool)
    u0 = max(0, u - half_window_px); u1 = min(w, u + half_window_px)
    v0 = max(0, v - half_window_px); v1 = min(h, v + half_window_px)
    wmask[v0:v1, u0:u1] = True
    valid = wmask & (depth > 0)
    cloud_masked = cloud_full[valid]
    if len(cloud_masked) < 100:
        return []

    # World-frame Z filter (e.g. cube_only). Convert cam-frame points to world,
    # filter by z, then convert back. cam2world is needed for both the filter
    # and the final grasp transform — compute once.
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3, :] = extr; extr = ext_h
    cam2world = np.linalg.inv(extr)

    if z_min is not None or z_max is not None:
        cm_h = np.concatenate([cloud_masked, np.ones((len(cloud_masked), 1))], axis=1)
        cm_world = (cam2world @ cm_h.T).T[:, :3]
        keep = np.ones(len(cm_world), dtype=bool)
        if z_min is not None:
            keep &= cm_world[:, 2] >= z_min
        if z_max is not None:
            keep &= cm_world[:, 2] <= z_max
        cloud_masked = cloud_masked[keep]
        if len(cloud_masked) < 100:
            return []

    if len(cloud_masked) >= num_point:
        idxs = np.random.choice(len(cloud_masked), num_point, replace=False)
    else:
        idxs1 = np.arange(len(cloud_masked))
        idxs2 = np.random.choice(len(cloud_masked), num_point - len(cloud_masked), replace=True)
        idxs = np.concatenate([idxs1, idxs2])
    cloud_sampled = cloud_masked[idxs]

    net, device = _load_net(checkpoint_path)
    end_points = {"point_clouds": torch.from_numpy(
        cloud_sampled[np.newaxis].astype(np.float32)).to(device)}
    with torch.no_grad():
        end_points = net(end_points)
        grasp_preds = pred_decode(end_points)
    gg_array = grasp_preds[0].detach().cpu().numpy()
    gg = GraspGroup(gg_array)
    if len(gg) == 0:
        return []
    gg.nms()
    gg.sort_by_score()
    top = gg[:top_k]

    out: list[dict[str, Any]] = []
    for g in top:
        # graspnetAPI grasp: translation (3,), rotation_matrix (3,3), depth, width, score
        t_cam = np.array([g.translation[0], g.translation[1], g.translation[2], 1.0])
        t_world = cam2world @ t_cam
        R_cam = np.asarray(g.rotation_matrix, dtype=np.float64)
        R_world = cam2world[:3, :3] @ R_cam
        from scipy.spatial.transform import Rotation as R
        quat = R.from_matrix(R_world).as_quat()  # [x,y,z,w]
        out.append({
            "score": float(g.score),
            "translation_world": [float(x) for x in t_world[:3]],
            "rotation_matrix_world": R_world.tolist(),
            "quat_xyzw_world": [float(q) for q in quat],
            "width": float(g.width),
            "depth": float(g.depth),
        })
    return out
