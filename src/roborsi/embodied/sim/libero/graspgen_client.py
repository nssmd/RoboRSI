"""Lightweight ZMQ client for an optional GraspGen inference service.

NVlabs/GraspGen runs in a separate conda env with torch 2.1+cu121 + spconv +
PointNet2 ops, exposed as a ZMQ server. This module is the lightweight client:
send an object point cloud over ZMQ and receive top-K 6-DoF grasps.

The service location comes from ``GRASPGEN_HOST`` and ``GRASPGEN_PORT``. See
the public reproduction guide for the optional server installation.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_CLIENT_CACHE: dict[str, Any] = {}
_BASE_TO_TCP_Z = 0.1034


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
    import msgpack
    import msgpack_numpy
    import zmq
    msgpack_numpy.patch()
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 30000)
    sock.setsockopt(zmq.SNDTIMEO, 10000)
    sock.connect(f"tcp://{host}:{port}")
    _CLIENT_CACHE[key] = (sock, msgpack)
    return _CLIENT_CACHE[key]


def _grasps_from_cloud(cloud_world, num_point=20000, top_k=3, host=None, port=None):
    """Run GraspGen on a pre-built world-frame object cloud."""
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

    from scipy.spatial.transform import Rotation
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
    out: list[dict[str, Any]] = []
    for i in range(min(top_k, len(grasps))):
        transform = grasps[i]
        rotation_world = transform[:3, :3]
        t_base = transform[:3, 3]
        approach = rotation_world[:, 2]
        t_tcp = t_base + approach * _BASE_TO_TCP_Z
        # Corrected EE orientation for aloha fl_link6 frame
        rotation_ee = np.column_stack(
            [rotation_world[:, 2], rotation_world[:, 0], rotation_world[:, 1]]
        )
        quat_xyzw_ee = Rotation.from_matrix(rotation_ee).as_quat()
        quat_wxyz_ee = [float(quat_xyzw_ee[3]), float(quat_xyzw_ee[0]),
                        float(quat_xyzw_ee[1]), float(quat_xyzw_ee[2])]
        # Also keep raw GraspGen quat for reference
        quat_xyzw_raw = Rotation.from_matrix(rotation_world).as_quat()
        out.append({
            "score": float(confidences[i]),
            "translation_world": [float(x) for x in t_tcp],
            "translation_tcp_world": [float(x) for x in t_tcp],
            "translation_base_world": [float(x) for x in t_base],   # GraspGen raw
            "rotation_matrix_world": rotation_world.tolist(),
            "rotation_matrix_ee": rotation_ee.tolist(),
            "quat_xyzw_world": [float(q) for q in quat_xyzw_raw],   # raw GraspGen quat
            "quat_wxyz_world": quat_wxyz_ee,   # aloha EE orientation (wxyz)
            "quat_world": quat_wxyz_ee,
            "approach_z": float(approach[2]),
            "num_object_points": int(len(cloud_world)),
            "width": 0.08, "depth": 0.02,
        })
    return out
