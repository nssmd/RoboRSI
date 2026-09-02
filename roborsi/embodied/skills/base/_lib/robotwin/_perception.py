"""CaP-X grasp-discipline perception helpers for RoboTwin (NEW, opt-in).

Ports the proven CaP-X pure-vision grasp discipline
(cap-x/capx/integrations/franka/libero.py) into RoboTwin BASE skills without
touching any existing file. Each function is a plain function over the sim
``impl`` (state.env._impl) — no VLM, no GT, no attach.

Pipeline (mirrors CaP-X):
  object_mask   -> Grounded-SAM bool mask of the named object (whole-scene reject)
  object_cloud  -> world-frame (N,3) cloud from mask + depth (reuse of
                   graspgen_infer.predict_grasps_with_mask's unprojection math)
  filter_noise  -> DBSCAN(eps=0.005, min_samples=10), keep the largest cluster
  object_obb    -> open3d OrientedBoundingBox (center, extent, R)
  topdown_grasp_from_obb -> a top-down grasp (x,y,z, quat_wxyz) whose fingers
                   close across the OBB's SHORTEST horizontal extent, TCP z at
                   the OBB body center (NOT the top face).

REUSE, not reimplementation:
  - object_cloud uses the SAME K/extrinsic_cv depth->camera->world math as
    graspgen_infer.predict_grasps_with_mask (depth in mm /1000, intrinsic_cv,
    extrinsic_cv, cam2world = inv(extrinsic)).
  - object_mask reuses base/detect_object's Grounded-DINO+SAM `detect`.
  - filter_noise / object_obb mirror CaP-X's FrankaLiberoApi.filter_noise and
    get_oriented_bounding_box_from_3d_points.

Quaternion convention (documented once, VERIFIED numerically — see
topdown_grasp_from_obb):
  RoboTwin / sapien / this codebase use WXYZ quaternions. The base top-down
  ("fingertip-down") gripper orientation is BASE_TOPDOWN_QUAT_WXYZ =
  [0.5, -0.5, 0.5, 0.5] (from winning traces). In the aloha fl_link6 EE frame
  (see graspgen_infer.py), the EE axes at that base quat map to world as:
    EE +X = APPROACH direction        -> world [0, 0, -1]  (straight down)
    EE +Y = JAW / finger-closing line -> world [-1, 0, 0]  (the world X axis)
    EE +Z                             -> world [0, 1, 0]
  So at yaw=0 the fingers close along the world **X axis**. A rotation of angle
  ``yaw`` about world +Z rotates the closing line to angle ``yaw`` measured from
  +X in the world XY plane, while the approach axis stays exactly straight-down
  (it is perpendicular to the XY plane, so a +Z yaw leaves it invariant). Hence
  yaw = atan2(dy, dx) of the desired closing direction aligns the jaw correctly.
  See topdown_grasp_from_obb for the composition + derivation.
"""
from __future__ import annotations

from typing import Any

import numpy as np


# RoboTwin top-down ("fingertip-down") gripper orientation, WXYZ. From winning
# traces (see base/robotwin move_fingertip_to / descend_tcp_to_z defaults).
BASE_TOPDOWN_QUAT_WXYZ = (0.5, -0.5, 0.5, 0.5)

# CaP-X DBSCAN parameters (libero.py FrankaLiberoApi.filter_noise).
_DBSCAN_EPS = 0.005
_DBSCAN_MIN_SAMPLES = 10

# Reject a SAM mask that covers more than this fraction of the frame — CaP-X
# lesson: SAM's top mask is often the whole table / scene, not the object.
_WHOLE_SCENE_MASK_FRAC = 0.40


def object_mask(impl, object_name: str, u: int | None = None,
                v: int | None = None, camera: str = "head_camera"
                ) -> np.ndarray | None:
    """Grounded-SAM bool mask (H, W) of the named object in ``camera``.

    Reuses base/detect_object's Grounding-DINO + SAM ``detect``. When (u, v) is
    given, selects the detection whose mask contains that pixel (disambiguates
    the instance when a same-named distractor exists — same rule as
    robotwin_tools._do_get_grasp_pose). Otherwise takes the top-scored
    detection.

    Whole-scene reject (CaP-X lesson): if the chosen mask covers more than
    ``_WHOLE_SCENE_MASK_FRAC`` of the frame it is almost certainly the table /
    background rather than the object -> return None so the caller aborts
    instead of building an OBB around the whole table.
    """
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    impl._update_render()
    impl.cameras.update_picture()
    rgb_entry = impl.cameras.get_rgb().get(camera)
    if rgb_entry is None:
        return None
    rgb = np.asarray(rgb_entry["rgb"] if isinstance(rgb_entry, dict) else rgb_entry)
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
               if rgb.max() <= 1 else rgb.astype(np.uint8))
    dets = detect(rgb, object_name, top_k=3)
    if not dets:
        return None
    mask = _pick_mask(dets, u, v)
    if mask is None:
        return None
    frac = float(mask.sum()) / float(mask.size)
    if frac > _WHOLE_SCENE_MASK_FRAC:
        # Whole-scene / table mask — reject (CaP-X discipline).
        return None
    return mask.astype(bool)


def _pick_mask(dets, u: int | None, v: int | None) -> np.ndarray | None:
    """Select the detection mask containing pixel (u,v); else the top-scored.

    Mirrors robotwin_tools._do_get_grasp_pose's instance-disambiguation: the
    top-score region is often a bigger distractor, so (u,v) picks the intended
    instance when provided.
    """
    if u is not None and v is not None:
        for d in dets:
            mh, mw = d.mask.shape
            if 0 <= int(v) < mh and 0 <= int(u) < mw and d.mask[int(v), int(u)]:
                return d.mask
        # No mask contained the pixel — take the detection whose centroid is
        # nearest to (u, v) rather than the top-score distractor.
        nearest = min(dets, key=lambda d: (d.centroid[0] - u) ** 2
                      + (d.centroid[1] - v) ** 2)
        return nearest.mask
    return dets[0].mask


def object_cloud(impl, mask: np.ndarray, camera: str = "head_camera",
                 z_min: float | None = None, z_max: float | None = None
                 ) -> np.ndarray:
    """World-frame (N,3) cloud of the masked object from ``camera``'s depth.

    Reuses the EXACT depth->camera->world unprojection of
    graspgen_infer.predict_grasps_with_mask: depth is millimetres (/1000),
    intrinsic_cv gives (fx, fy, cx, cy), extrinsic_cv is world->camera so
    cam2world = inv(extrinsic). Returns an empty (0,3) array if no valid
    on-object depth pixels.
    """
    impl._update_render()
    impl.cameras.update_picture()
    config = impl.cameras.get_config().get(camera)
    depth_entry = impl.cameras.get_depth().get(camera)
    if config is None or depth_entry is None:
        return np.zeros((0, 3))
    depth = depth_entry["depth"] if isinstance(depth_entry, dict) else depth_entry
    depth = np.asarray(depth)
    if mask.shape != depth.shape:
        raise ValueError(f"mask shape {mask.shape} != depth shape {depth.shape}")
    valid = mask.astype(bool) & (depth > 0)
    if valid.sum() < 30:
        return np.zeros((0, 3))
    cloud_world = _unproject_valid(config, depth, valid)
    if z_min is not None:
        cloud_world = cloud_world[cloud_world[:, 2] >= z_min]
    if z_max is not None:
        cloud_world = cloud_world[cloud_world[:, 2] <= z_max]
    return cloud_world


def _unproject_valid(config: dict, depth: np.ndarray, valid: np.ndarray
                     ) -> np.ndarray:
    """depth[valid] -> world cloud. Same math as predict_grasps_with_mask."""
    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        ext_h = np.eye(4)
        ext_h[:3, :] = extr
        extr = ext_h
    cam2world = np.linalg.inv(extr)
    vs, us = np.where(valid)
    z = depth[vs, us].astype(np.float64) / 1000.0
    x = (us - cx) * z / fx
    y = (vs - cy) * z / fy
    cloud_cam = np.stack([x, y, z], axis=1)
    cm_h = np.concatenate([cloud_cam, np.ones((len(cloud_cam), 1))], axis=1)
    return (cam2world @ cm_h.T).T[:, :3]


def filter_noise(points: np.ndarray, eps: float = _DBSCAN_EPS,
                 min_samples: int = _DBSCAN_MIN_SAMPLES) -> np.ndarray:
    """DBSCAN denoise: drop label -1, KEEP THE LARGEST cluster.

    CaP-X's FrankaLiberoApi.filter_noise only drops label -1 (noise); a masked
    object cloud can still carry a satellite blob (mask bleed onto table / a
    neighbour). Keeping the LARGEST cluster additionally sheds that blob so the
    OBB fits the object body, not object+blob. Falls back to the raw points if
    too few to cluster.
    """
    from sklearn.cluster import DBSCAN
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < min_samples:
        return pts
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(pts)
    keep = labels != -1
    if not keep.any():
        return np.zeros((0, 3))
    kept_labels = labels[keep]
    values, counts = np.unique(kept_labels, return_counts=True)
    largest = values[int(np.argmax(counts))]
    return pts[labels == largest]


def object_obb(points: np.ndarray) -> dict[str, Any]:
    """Oriented bounding box of a 3D point cloud via open3d.

    Mirrors CaP-X common.get_oriented_bounding_box_from_3d_points: inject a
    tiny isotropic jitter (open3d's OBB is degenerate on a coplanar / rank-<3
    cloud, e.g. a single-view flat face), drop statistical outliers, then fit.
    Returns {center (3,), extent (3,), R (3,3)}.
    """
    import open3d as o3d
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 4:
        raise ValueError(f"need >=4 points for an OBB, got {len(pts)}")
    pts = pts + np.random.normal(0.0, 1e-4, pts.shape)
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(pts)
    pc, _ind = pc.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    obb = pc.get_oriented_bounding_box()
    return {
        "center": np.asarray(obb.center, dtype=np.float64),
        "extent": np.asarray(obb.extent, dtype=np.float64),
        "R": np.asarray(obb.R, dtype=np.float64),
    }


def topdown_grasp_from_obb(obb: dict[str, Any]
                           ) -> tuple[float, float, float, tuple]:
    """Top-down grasp from an OBB -> (x, y, z, quat_wxyz).

    Convention (see module docstring): quaternions are WXYZ; the base top-down
    orientation is BASE_TOPDOWN_QUAT_WXYZ = [0.5, -0.5, 0.5, 0.5], and a yaw
    about world +Z rotates the finger-closing line by that yaw in the XY plane.

    Discipline (CaP-X): fingers close across the OBB's SHORTEST HORIZONTAL
    extent so the jaws straddle the narrow dimension of a box/cylinder; the TCP
    descends to the OBB BODY-CENTER height (obb["center"][2]), NOT the top face
    — descend INTO the body for a firmer grip.

    Yaw derivation:
      The OBB's three body axes are the columns of R with half-lengths
      extent/2. Project each axis onto the world XY plane and pair it with its
      extent. The two axes with the largest horizontal projection are the
      object's footprint axes; of those pick the one with the SHORTER extent —
      that horizontal direction is where the fingers must close (straddle the
      narrow side). Its world-XY angle atan2(dy, dx) is the closing-line yaw.

      A pure +Z yaw of the base top-down quat rotates the closing line to that
      angle. At the base quat (yaw=0) the jaw (EE +Y) already lies along the
      world +X axis (see module docstring), so yaw=0 IS the +X reference —
      there is no base offset to subtract, and yaw = atan2(dy, dx) directly
      aligns the jaw with the target closing direction. (The 180-deg jaw
      ambiguity is harmless: closing along +d or -d is identical.)

      Numerically verified: for a narrow-Y box the composed quat's jaw axis is
      world Y and the approach axis stays world -Z; for a narrow-X box the jaw
      axis is world X — both with the approach exactly straight-down.
    """
    center = np.asarray(obb["center"], dtype=np.float64)
    extent = np.asarray(obb["extent"], dtype=np.float64)
    R = np.asarray(obb["R"], dtype=np.float64)
    dx, dy = _closing_direction_xy(R, extent)
    yaw = float(np.arctan2(dy, dx))
    quat_wxyz = _compose_topdown_yaw(yaw)
    x, y, z = float(center[0]), float(center[1]), float(center[2])
    return x, y, z, quat_wxyz


def _closing_direction_xy(R: np.ndarray, extent: np.ndarray
                          ) -> tuple[float, float]:
    """World-XY unit vector of the finger-closing line = the SHORTER of the two
    most-horizontal OBB axes, projected to XY.

    Each OBB axis is a column of R; ``horizontal[i]`` is how horizontal that
    axis is (XY-projection length). The two largest-horizontal axes are the
    footprint axes; the one with the SMALLER extent is the narrow footprint
    dimension the jaws should straddle.
    """
    horizontal = np.hypot(R[0, :], R[1, :])          # XY-projection length per axis
    order = np.argsort(-horizontal)                  # most-horizontal axes first
    a, b = int(order[0]), int(order[1])              # the two footprint axes
    narrow = a if extent[a] <= extent[b] else b      # shorter horizontal extent
    axis = R[:, narrow]
    dx, dy = float(axis[0]), float(axis[1])
    norm = float(np.hypot(dx, dy))
    if norm < 1e-9:
        return 1.0, 0.0
    return dx / norm, dy / norm


def _compose_topdown_yaw(yaw: float) -> tuple[float, float, float, float]:
    """q_yaw(+Z) ⊗ base-top-down, returned WXYZ.

    q_yaw is a rotation of ``yaw`` about world +Z: wxyz = (cos(yaw/2), 0, 0,
    sin(yaw/2)). Compose (world-frame pre-multiply) with BASE_TOPDOWN_QUAT_WXYZ
    so the fingertip-down orientation is preserved and only the in-plane jaw
    direction rotates.
    """
    qz = (float(np.cos(yaw / 2.0)), 0.0, 0.0, float(np.sin(yaw / 2.0)))
    return _quat_mul_wxyz(qz, BASE_TOPDOWN_QUAT_WXYZ)


def _quat_mul_wxyz(qa, qb) -> tuple[float, float, float, float]:
    """Hamilton product of two WXYZ quaternions -> WXYZ."""
    aw, ax, ay, az = (float(v) for v in qa)
    bw, bx, by, bz = (float(v) for v in qb)
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


# ── Placement discipline (CaP-X §4.2: place is a SECOND perception problem) ──

def container_drop_target(obb: dict[str, Any], inset_m: float = 0.01
                          ) -> tuple[float, float, float]:
    """Interior drop point (x, y, drop_z) for depositing a held object into a
    container localized by its OBB.

    CaP-X discipline: release OVER the container's interior CENTER (its OBB
    center XY — away from the walls, above the cavity mouth) at a bbox-computed
    drop height. The drop z is the container's RIM TOP (OBB center_z +
    extent_z/2) lowered by ``inset_m`` so the fingertips clear the rim and the
    object is released just inside the mouth (not above it), yet not so deep the
    jaws hit the rim walls. Descend to this z at the center, then release.
    """
    center = np.asarray(obb["center"], dtype=np.float64)
    extent = np.asarray(obb["extent"], dtype=np.float64)
    rim_top = float(center[2] + extent[2] / 2.0)
    return float(center[0]), float(center[1]), rim_top - float(inset_m)


def point_inside_footprint(point, obb: dict[str, Any], xy_tol: float = 0.02,
                           z_tol: float = 0.02) -> bool:
    """True iff ``point`` lies inside the container OBB's horizontal footprint
    AND at/below its rim top — a DEPTH containment test the 2D find_pixel∈bbox
    check cannot fake (a cube resting ON the rim projects inside the 2D bbox but
    sits ABOVE the rim in z, so this rejects it).

    Transforms the point into the OBB body frame (R.T @ (p - center)): the x/y
    body coordinates must fall within extent/2 (+xy_tol) and the world z must be
    at or below rim_top (+z_tol).
    """
    p = np.asarray(point, dtype=np.float64)
    center = np.asarray(obb["center"], dtype=np.float64)
    extent = np.asarray(obb["extent"], dtype=np.float64)
    R = np.asarray(obb["R"], dtype=np.float64)
    body = R.T @ (p - center)
    in_xy = (abs(float(body[0])) <= extent[0] / 2.0 + xy_tol
             and abs(float(body[1])) <= extent[1] / 2.0 + xy_tol)
    rim_top = float(center[2] + extent[2] / 2.0)
    below_rim = float(p[2]) <= rim_top + z_tol
    return bool(in_xy and below_rim)


def cloud_centroid(points) -> np.ndarray:
    """Mean XYZ of a (N,3) cloud (the placed object's world-frame center)."""
    return np.asarray(points, dtype=np.float64).mean(axis=0)


def container_opening(points, rim_band_m: float = 0.02
                      ) -> dict[str, Any] | None:
    """Fit the RIM / OPENING FRAME of a top-open container from its world cloud —
    the placement analogue of an OBB grasp axis (container-placement literature:
    an opening frame = center + rim height + in-plane extents is the compact
    action target for depositing into cavities).

    A head-down camera sees a top-open container mostly as its RIM ring plus the
    visible interior. The rim is the highest band of points; its XY centroid is
    the true OPENING CENTER (unbiased by thick walls / an asymmetric solid, which
    skew a whole-cloud OBB center), and its z is the rim height to release at.

    Returns {center: [cx, cy, rim_z], rim_z, half_extents: [hx, hy]} or None if
    the cloud is too small / degenerate to isolate a rim band."""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 12:
        return None
    rim_z = float(np.percentile(pts[:, 2], 90))
    rim = pts[pts[:, 2] >= rim_z - rim_band_m]
    if len(rim) < 6:
        return None
    cx, cy = float(rim[:, 0].mean()), float(rim[:, 1].mean())
    hx = float(np.percentile(np.abs(rim[:, 0] - cx), 90))
    hy = float(np.percentile(np.abs(rim[:, 1] - cy), 90))
    return {"center": [cx, cy, rim_z], "rim_z": rim_z, "half_extents": [hx, hy]}
