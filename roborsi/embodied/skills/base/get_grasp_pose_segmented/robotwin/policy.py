"""base.robotwin.get_grasp_pose_segmented — color-segmented GraspGen.

VLM-callable via robotwin_agent auto-dispatch (looks up dispatch_runtime).
Filters head_camera point cloud to ONLY pixels matching the named color
before sending to GraspGen, so GraspGen returns grasps on the colored
target object instead of surrounding container geometry.
"""

from __future__ import annotations

from typing import Any

import numpy as np


# HSV ranges (OpenCV: H in 0-179, S/V in 0-255). Tuned for sapien/RoboTwin
# rendered colors. Each entry is a list of (lo, hi) tuples — red wraps
# around hue=0 so it needs two ranges.
_COLOR_HSV: dict[str, list[tuple[tuple[int, int, int], tuple[int, int, int]]]] = {
    "red":    [((0, 100, 60), (12, 255, 255)), ((168, 100, 60), (179, 255, 255))],
    "green":  [((40, 60, 40), (85, 255, 255))],
    "blue":   [((95, 90, 50), (130, 255, 255))],
    "yellow": [((20, 100, 80), (35, 255, 255))],
    "orange": [((8, 120, 80), (22, 255, 255))],
    "purple": [((128, 50, 40), (160, 255, 255))],
}


def dispatch_runtime(state, args: dict[str, Any]):
    import cv2
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_find_pixel

    obj = args.get("object")
    color = str(args.get("color", "")).lower()
    cam_name = args.get("camera", "head_camera")
    pad = int(args.get("bbox_pad_px", 30))
    top_k = int(args.get("top_k", 5))
    if not obj:
        return ({"ok": False, "reason": "object name required"}, _snapshot(state.env))
    if color not in _COLOR_HSV:
        return ({"ok": False, "reason": f"color '{color}' not in {list(_COLOR_HSV)}"},
                _snapshot(state.env))

    coarse, _ = _do_find_pixel(state, {"object": obj, "location": "center"})
    if not coarse.get("ok"):
        return ({"ok": False, "reason": f"coarse find_pixel failed: {coarse.get('reason')}"},
                _snapshot(state.env))
    u_c, v_c = int(coarse["u"]), int(coarse["v"])

    impl = state.env._impl
    impl._update_render()
    impl.cameras.update_picture()
    rgb = impl.cameras.get_rgb().get(cam_name, {}).get("rgb")
    depth = impl.cameras.get_depth().get(cam_name, {}).get("depth")
    config = impl.cameras.get_config().get(cam_name)
    if depth is None or config is None or rgb is None:
        return ({"ok": False, "reason": f"camera '{cam_name}' missing rgb/depth/config"},
                _snapshot(state.env))
    h, w = depth.shape

    if rgb.dtype != np.uint8:
        rgb = (rgb * 255).clip(0, 255).astype(np.uint8) if float(rgb.max()) <= 1.0 else rgb.astype(np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    color_mask = np.zeros((h, w), dtype=bool)
    for lo, hi in _COLOR_HSV[color]:
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        color_mask |= (m > 0)
    bbox_mask = np.zeros((h, w), dtype=bool)
    u0, u1 = max(0, u_c - pad), min(w, u_c + pad)
    v0, v1 = max(0, v_c - pad), min(h, v_c + pad)
    bbox_mask[v0:v1, u0:u1] = True
    valid = color_mask & bbox_mask & (depth > 0)
    n_pts = int(valid.sum())
    if n_pts < 30:
        return ({"ok": False, "reason": f"only {n_pts} colored points in bbox — "
                 f"color mask too tight, bbox too small, or wrong color name",
                 "num_object_points": n_pts}, _snapshot(state.env))

    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    fx, fy = K[0, 0], K[1, 1]
    cx_k, cy_k = K[0, 2], K[1, 2]
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3, :] = extr; extr = ext_h
    cam2world = np.linalg.inv(extr)

    vs, us = np.where(valid)
    z_cam = depth[vs, us].astype(np.float64) / 1000.0
    x_cam = (us - cx_k) * z_cam / fx
    y_cam = (vs - cy_k) * z_cam / fy
    cloud_cam = np.stack([x_cam, y_cam, z_cam], axis=1)
    cloud_h = np.concatenate([cloud_cam, np.ones((len(cloud_cam), 1))], axis=1)
    cloud_world = (cam2world @ cloud_h.T).T[:, :3].astype(np.float32)
    if len(cloud_world) > 8000:
        idxs = np.random.choice(len(cloud_world), 8000, replace=False)
        cloud_world = cloud_world[idxs]

    import os, msgpack
    import msgpack_numpy
    msgpack_numpy.patch()
    import zmq
    host = os.environ.get("GRASPGEN_HOST", "localhost")
    port = int(os.environ.get("GRASPGEN_PORT", "5556"))
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 30000)
    sock.setsockopt(zmq.SNDTIMEO, 10000)
    sock.connect(f"tcp://{host}:{port}")
    request = {"action": "infer", "point_cloud": cloud_world,
               "num_grasps": 200, "topk_num_grasps": top_k}
    sock.send(msgpack.packb(request, use_bin_type=True))
    reply = msgpack.unpackb(sock.recv(), raw=False)
    grasps = np.asarray(reply.get("grasps"))
    confidences = np.asarray(reply.get("confidences"))
    if grasps.size == 0:
        return ({"ok": False, "reason": "GraspGen returned no grasps for segmented cloud",
                 "num_object_points": n_pts}, _snapshot(state.env))

    from scipy.spatial.transform import Rotation as R
    from roborsi.embodied.sim.robotwin.gripper_geom import ALOHA_TCP_IN_EE_LOCAL
    # Mesh-measured flange→TCP offset (URDF + link7.STL). Replaces legacy 0.18.
    FINGER_OFFSET = float(ALOHA_TCP_IN_EE_LOCAL[0])
    candidates = []
    for i in range(len(grasps)):
        T = grasps[i]
        R_world = T[:3, :3]
        t_tcp = T[:3, 3]
        approach = R_world[:, 2]
        t_flange = t_tcp - FINGER_OFFSET * approach
        quat = R.from_matrix(R_world).as_quat()
        candidates.append({
            "score": float(confidences[i]),
            "translation_world": [float(x) for x in t_flange],
            "translation_tcp_world": [float(x) for x in t_tcp],
            "quat_xyzw_world": [float(q) for q in quat],
            "approach_z": float(approach[2]),
            "width": 0.08, "depth": 0.02,
        })
    # Prefer top-down (approach.z near -1, i.e. fingers point DOWN in world).
    # Aloha-agilex's wrist can't reach upward-approach grasps. Sort so most
    # downward-pointing candidates come first; ties broken by GraspGen score.
    candidates.sort(key=lambda c: (c["approach_z"], -c["score"]))
    candidates = candidates[:top_k]
    top = candidates[0]
    pose = [*top["translation_world"], *top["quat_xyzw_world"]]
    return ({"ok": True, "backend": "graspgen_segmented",
             "grasp_pose": pose, "score": top["score"],
             "approach_z": top["approach_z"],
             "candidates": candidates,
             "num_object_points": n_pts,
             "note": f"GraspGen on {n_pts}-point '{color}' segmented cloud, "
                     f"sorted top-down-first. Top approach_z={top['approach_z']:.3f} "
                     f"(should be < -0.5 for clean top-down). "
                     f"Pass grasp_pose into move_to_pose."},
            _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
