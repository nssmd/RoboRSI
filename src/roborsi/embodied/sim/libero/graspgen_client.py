"""Lightweight ZMQ client for an optional GraspGen inference service.

NVlabs/GraspGen runs in a separate conda env with torch 2.1+cu121 + spconv +
PointNet2 ops, exposed as a ZMQ server. This module is the lightweight client:
build a world-frame point cloud around a target pixel, send it over ZMQ, get
back top-K 6-DoF grasps + confidence.

The service location comes from ``GRASPGEN_HOST`` and ``GRASPGEN_PORT``. See
the public reproduction guide for the optional server installation.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np


_CLIENT_CACHE: dict[str, Any] = {}


def _discard_client(host: str, port: int, socket: Any) -> None:
    key = f"{host}:{port}"
    cached = _CLIENT_CACHE.get(key)
    if cached is not None and cached[0] is socket:
        _CLIENT_CACHE.pop(key, None)
    try:
        socket.close(linger=0)
    except Exception:  # noqa: BLE001
        pass


def _client(host: str, port: int):
    key = f"{host}:{port}"
    if key in _CLIENT_CACHE:
        return _CLIENT_CACHE[key]
    # Lightweight client — only needs pyzmq + msgpack. The roborsi-sim
    # process should have these installed (cheap deps, no CUDA).
    import zmq
    import msgpack
    import msgpack_numpy
    msgpack_numpy.patch()
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 30000)
    sock.setsockopt(zmq.SNDTIMEO, 10000)
    sock.connect(f"tcp://{host}:{port}")
    _CLIENT_CACHE[key] = (sock, msgpack)
    return _CLIENT_CACHE[key]


def _complete_symmetric_cloud(cloud: np.ndarray, cam_pos: np.ndarray) -> np.ndarray:
    """Synthesize the occluded FAR side of a vertical rotationally-symmetric object
    (can / bottle / roller) by mirroring the visible front shell across the vertical
    plane through the estimated axis, perpendicular to the camera view ray.

    A single head-camera view only captures the camera-FACING surface, so the cloud's
    centroid sits ~radius in FRONT of the true axis — GraspGen then places grasps on the
    front shell and the fingers close in front of the object's body, shoving it. Mirroring
    makes the cloud symmetric: its centroid lands on the axis and GraspGen sees a full
    cylinder, so its grasps center on the object. Pure geometry, no GT."""
    if len(cloud) < 30:
        return cloud
    c = cloud.mean(axis=0)
    view = c - cam_pos
    view[2] = 0.0
    n = float(np.linalg.norm(view))
    if n < 1e-6:
        return cloud
    view = view / n
    lat = np.array([-view[1], view[0], 0.0])          # horizontal, ⊥ view ray
    rel = cloud - c
    lat_extent = float(np.percentile(rel @ lat, 95) - np.percentile(rel @ lat, 5))
    radius = float(np.clip(0.5 * lat_extent, 0.01, 0.06))
    axis_c = c + view * radius                         # true axis xy, behind the shell
    d = (cloud - axis_c) @ view                        # signed depth along view ray
    mirrored = cloud - 2.0 * np.outer(d, view)         # reflect across the axis plane
    return np.concatenate([cloud, mirrored], axis=0)


def predict_grasps_with_mask(
    impl, camera_name: str, mask: np.ndarray,
    num_point: int = 20000,
    top_k: int = 3,
    z_min: float | None = None,
    z_max: float | None = None,
    host: str | None = None,
    port: int | None = None,
    complete_symmetric: bool = False,
) -> list[dict[str, Any]]:
    """Build the GraspGen cloud from a 2D object mask.

    Unprojecting only on-object pixels avoids degenerate edge grasps caused by
    bounding-box crops dominated by the table or background.
    """
    host = host or os.environ.get("GRASPGEN_HOST", "localhost")
    port = port or int(os.environ.get("GRASPGEN_PORT", "5556"))

    impl._update_render()
    impl.cameras.update_picture()
    config = impl.cameras.get_config().get(camera_name)
    depth = impl.cameras.get_depth().get(camera_name, {}).get("depth")
    if config is None or depth is None:
        raise RuntimeError(f"camera '{camera_name}' missing config/depth")
    if mask.shape != depth.shape:
        raise ValueError(f"mask shape {mask.shape} != depth shape {depth.shape}")

    valid = mask.astype(bool) & (depth > 0)
    if valid.sum() < 30:
        return []

    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3, :] = extr; extr = ext_h
    cam2world = np.linalg.inv(extr)

    vs, us = np.where(valid)
    z = depth[vs, us].astype(np.float64) / 1000.0
    x = (us - cx) * z / fx
    y = (vs - cy) * z / fy
    cloud_cam = np.stack([x, y, z], axis=1)
    cm_h = np.concatenate([cloud_cam, np.ones((len(cloud_cam), 1))], axis=1)
    cloud_world = (cam2world @ cm_h.T).T[:, :3]
    if z_min is not None:
        cloud_world = cloud_world[cloud_world[:, 2] >= z_min]
    if z_max is not None:
        cloud_world = cloud_world[cloud_world[:, 2] <= z_max]
    if len(cloud_world) < 30:
        return []
    if complete_symmetric:
        _cam_pos = (cam2world @ np.array([0.0, 0.0, 0.0, 1.0]))[:3]
        cloud_world = _complete_symmetric_cloud(cloud_world, _cam_pos)
    return _grasps_from_cloud(cloud_world, num_point=num_point, top_k=top_k,
                             host=host, port=port)


def _grasps_from_cloud(cloud_world, num_point=20000, top_k=3, host=None, port=None):
    """Run GraspGen on a pre-built world-frame OBJECT cloud -> aloha EE grasp dicts.
    Factored out of predict_grasps_with_mask so a FUSED multi-camera cloud reuses
    the same normalize->send->convert path (real multi-view, not just one view)."""
    host = host or os.environ.get("GRASPGEN_HOST", "localhost")
    port = port or int(os.environ.get("GRASPGEN_PORT", "5556"))
    cloud_world = np.asarray(cloud_world, dtype=np.float32)
    if len(cloud_world) < 30:
        return []
    if len(cloud_world) > num_point:
        idxs = np.random.choice(len(cloud_world), num_point, replace=False)
        cloud_world = cloud_world[idxs]
    cloud_world = cloud_world.astype(np.float32)

    # GraspGen was trained on point clouds centered near the origin (Z≈0).
    # Our world-frame cloud is at Z≈0.74 (table height). To avoid distribution
    # shift that causes GraspGen to output wrong approach directions (all +Z
    # instead of sensible top-down -Z), normalize the cloud centroid to origin
    # before inference and translate output grasp positions back afterward.
    cloud_centroid = cloud_world.mean(axis=0)
    cloud_normalized = cloud_world - cloud_centroid

    sock, msgpack = _client(host, port)
    try:
        sock.send(msgpack.packb({
            "action": "infer", "point_cloud": cloud_normalized,
            "num_grasps": 400, "topk_num_grasps": top_k,
        }, use_bin_type=True))
        reply = msgpack.unpackb(sock.recv(), raw=False)
    except Exception:
        _discard_client(host, port, sock)
        raise
    raw_grasps = reply.get("grasps") if isinstance(reply, dict) else None
    raw_confidences = reply.get("confidences") if isinstance(reply, dict) else None
    if raw_grasps is None or raw_confidences is None:
        return []
    grasps = np.asarray(raw_grasps)
    confidences = np.asarray(raw_confidences).reshape(-1)
    if (
        grasps.ndim != 3
        or grasps.shape[1:] != (4, 4)
        or len(grasps) == 0
        or len(confidences) < len(grasps)
    ):
        return []

    # Translate grasp base-link positions back to world frame
    grasps = grasps.copy()
    grasps[:, :3, 3] += cloud_centroid

    from scipy.spatial.transform import Rotation as R
    # GraspGen pose convention (from docs/GRIPPER_DESCRIPTION.md):
    #   - origin = gripper BASE LINK (wrist), not TCP
    #   - +Z = approach axis (gripper approaches object along +Z)
    #   - +X = finger closing direction (jaw opens/closes along ±X)
    #   - "depth" = distance from base link to TCP along +Z, per config YAML
    # For our checkpoint graspgen_franka_panda.yml: gripper_depth = 0.1034 m
    #
    # Aloha fl_link6 EE frame (from arx5_description_isaac.urdf + mesh):
    #   - +X = approach direction (fingertips extend along +X; joint origins at X=0.0846)
    #   - +Y = jaw direction (fl_link7 at +0.0245 Y, fl_link8 at -0.0245 Y)
    #
    # Convention mapping — GraspGen→Aloha EE:
    #   GraspGen +Z (approach) → aloha +X
    #   GraspGen +X (jaw)      → aloha +Y
    #   GraspGen +Y            → aloha +Z  (right-hand: cross(+Z,+X) = +Y in GraspGen
    #                                         maps to cross(+X,+Y) = +Z in aloha)
    # So: R_ee = column_stack([R_world[:,2], R_world[:,0], R_world[:,1]])
    GRASPGEN_BASE_TO_TCP_Z = 0.1034   # franka_panda gripper_depth from yml
    out: list[dict[str, Any]] = []
    for i in range(min(top_k, len(grasps))):
        T = grasps[i]
        R_world = T[:3, :3]
        t_base = T[:3, 3]
        approach = R_world[:, 2]
        t_tcp = t_base + approach * GRASPGEN_BASE_TO_TCP_Z
        # Corrected EE orientation for aloha fl_link6 frame
        R_ee = np.column_stack([R_world[:, 2], R_world[:, 0], R_world[:, 1]])
        quat_xyzw_ee = R.from_matrix(R_ee).as_quat()
        quat_wxyz_ee = [float(quat_xyzw_ee[3]), float(quat_xyzw_ee[0]),
                        float(quat_xyzw_ee[1]), float(quat_xyzw_ee[2])]
        # Also keep raw GraspGen quat for reference
        quat_xyzw_raw = R.from_matrix(R_world).as_quat()
        out.append({
            "score": float(confidences[i]),
            "translation_world": [float(x) for x in t_tcp],
            "translation_tcp_world": [float(x) for x in t_tcp],
            "translation_base_world": [float(x) for x in t_base],   # GraspGen raw
            "rotation_matrix_world": R_world.tolist(),               # GraspGen axes
            "rotation_matrix_ee": R_ee.tolist(),                     # aloha fl_link6 axes
            "quat_xyzw_world": [float(q) for q in quat_xyzw_raw],   # raw GraspGen quat
            "quat_wxyz_world": quat_wxyz_ee,   # aloha EE orientation (wxyz)
            "quat_world": quat_wxyz_ee,
            "approach_z": float(approach[2]),
            "num_object_points": int(len(cloud_world)),
            "width": 0.08, "depth": 0.02,
        })
    return out
