"""base.robotwin.segment_object_pointcloud — clean multi-view object cloud.

For each camera:
  1. Grounded-SAM detect(object) → mask
  2. (optional) Sub-VLM yes/no on the masked crop: "is this really {object}?"
     Filters out GroundingDINO false-positives (e.g. red robot base
     detected as 'coloured block', dark gripper detected as 'hammer').
  3. Unproject mask pixels to world XYZ via depth + camera extrinsics.
  4. (optional) Drop points farther than ee_radius_m from ee_xyz.

Fuse surviving clouds across cameras → clean cloud for downstream PCA /
functional-point estimation / grasp synthesis.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import numpy as np


_DEFAULT_CAMS = ("head_camera", "front_camera", "left_camera", "right_camera")


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    obj_arg = args.get("object")
    if isinstance(obj_arg, str):
        queries = [obj_arg.strip()] if obj_arg.strip() else []
    elif isinstance(obj_arg, list):
        queries = [str(q).strip() for q in obj_arg if str(q).strip()]
    else:
        queries = []
    if not queries:
        return ({"ok": False, "reason": "object required (str or list[str])"},
                _snapshot(state.env))
    obj = queries[0]  # primary label used in VLM prompts
    ee_xyz = args.get("ee_xyz")
    ee_xyz_np = np.asarray(ee_xyz, dtype=np.float64) if ee_xyz is not None else None
    ee_radius_m = float(args.get("ee_radius_m", 0.20))
    vlm_verify = bool(args.get("vlm_verify", True))
    min_pixels = int(args.get("min_pixels", 30))
    cameras = args.get("cameras") or list(_DEFAULT_CAMS)
    max_points = int(args.get("max_points", 5000))

    impl = state.env._impl
    impl._update_render(); impl.cameras.update_picture()
    rgb_all = impl.cameras.get_rgb()
    depth_all = impl.cameras.get_depth()
    cfg_all = impl.cameras.get_config()

    per_cam: dict[str, dict[str, Any]] = {}
    fused: list[np.ndarray] = []
    cams_used: list[str] = []

    workdir = Path(getattr(state, "workdir", "/tmp/segobj")) / "seg_crops"
    workdir.mkdir(parents=True, exist_ok=True)

    for cam in cameras:
        rec: dict[str, Any] = {"vlm_verdict": "skipped", "contribution_pts": 0}
        per_cam[cam] = rec
        if cam not in rgb_all or cam not in depth_all or cam not in cfg_all:
            rec["error"] = "camera not available"
            continue
        rgb = rgb_all[cam]["rgb"]; depth = depth_all[cam]["depth"]; cfg = cfg_all[cam]
        if rgb.dtype != np.uint8:
            rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
                   if rgb.max() <= 1 else rgb.astype(np.uint8))
        sam = _sam_detect_multi(rgb, queries)
        if sam is None:
            rec["error"] = "no SAM detection (any query)"
            continue
        mask, scores, per_query = sam
        rec["sam_score"] = round(float(max(scores)), 3)
        rec["sam_per_query"] = per_query
        rec["n_mask_pix"] = int(mask.sum())
        if rec["n_mask_pix"] < min_pixels:
            rec["error"] = f"mask only {rec['n_mask_pix']} pix < {min_pixels}"
            continue

        if vlm_verify:
            ok, why, crop_path = _vlm_verify_mask(rgb, mask, obj, cam, workdir)
            rec["vlm_verdict"] = ("yes" if ok else "no") + f": {why[:80]}"
            rec["vlm_crop_path"] = str(crop_path)
            if not ok:
                continue
        else:
            rec["vlm_verdict"] = "off"

        world = _unproject_mask(depth, mask, cfg)
        if world is None or len(world) < 3:
            rec["error"] = "no valid depth in mask"
            continue
        if ee_xyz_np is not None:
            d = np.linalg.norm(world - ee_xyz_np[None, :], axis=1)
            world = world[d <= ee_radius_m]
            rec["n_after_ee_filter"] = int(len(world))
            if len(world) < 3:
                rec["error"] = f"all {rec['n_mask_pix']} pix outside ee_radius={ee_radius_m}"
                continue
        rec["contribution_pts"] = int(len(world))
        fused.append(world)
        cams_used.append(cam)

    if not fused:
        return ({"ok": False, "reason": f"no camera produced valid '{obj}' cloud",
                 "per_camera": per_cam}, _snapshot(state.env))

    cloud = np.concatenate(fused, axis=0)

    # Spatial declustering: SAM cross-camera can leak in a NEIGHBOR object
    # (e.g. the red coloured_block 15cm away gets segmented as 'hammer' in
    # right_camera). Within ee_radius these survive. DBSCAN clusters the
    # fused cloud; keep only the cluster whose centroid is CLOSEST to
    # ee_xyz (the actual held tool / queried object location).
    cluster_strategy = str(args.get("cluster_strategy", "vlm"))
    cluster_note = ""
    if ee_xyz_np is not None and len(cloud) > 50:
        cloud_kept, cluster_note = _select_clusters(
            impl, cloud, ee_xyz_np, obj, eps_m=0.05, min_samples=20,
            strategy=cluster_strategy, workdir=workdir)
        if cloud_kept is not None and len(cloud_kept) >= 50:
            cloud = cloud_kept

    if len(cloud) > max_points:
        idx = np.random.default_rng(0).choice(len(cloud), size=max_points, replace=False)
        cloud = cloud[idx]
    centroid = cloud.mean(axis=0).tolist()

    return ({"ok": True,
             "n_points": int(len(cloud)),
             "centroid": [round(float(c), 4) for c in centroid],
             "cameras_used": cams_used,
             "per_camera": per_cam,
             "cluster_note": cluster_note,
             "xyz": cloud.tolist(),
             "note": (f"Clean fused cloud for '{obj}': {len(cloud)} points "
                      f"from {len(cams_used)} cameras (SAM+VLM-verified+ee-radius "
                      f"filtered, kept-nearest-cluster). "
                      f"centroid={[round(c,3) for c in centroid]}.")},
            _snapshot(state.env))


def _select_clusters(impl, cloud: np.ndarray, ee_xyz: np.ndarray,
                      obj: str, eps_m: float, min_samples: int,
                      strategy: str, workdir: Path,
                      ) -> tuple[np.ndarray | None, str]:
    """DBSCAN the cloud and pick clusters by strategy. New default 'vlm'
    asks the VLM to look at color-coded clusters projected on a camera and
    select the one(s) that ARE the named object — replaces the previous
    hand-tuned 'containing_ee N cm' threshold (which broke when the tap
    TARGET was within EE radius)."""
    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        return cloud, "DBSCAN skipped (sklearn unavailable)"
    labels = DBSCAN(eps=eps_m, min_samples=min_samples, n_jobs=1).fit_predict(cloud)
    uniq = sorted({int(c) for c in set(labels) if int(c) != -1})
    if not uniq:
        return cloud, f"DBSCAN: no dense cluster (eps={eps_m}m)"
    if len(uniq) == 1:
        return cloud[labels == uniq[0]], f"DBSCAN: 1 cluster (n={int((labels==uniq[0]).sum())})"

    summaries = []
    for c in uniq:
        m = labels == c
        cent = cloud[m].mean(axis=0)
        summaries.append({"label": c, "n": int(m.sum()), "centroid": cent,
                           "d_centroid": float(np.linalg.norm(cent - ee_xyz)),
                           "d_min_to_ee": float(np.linalg.norm(cloud[m] - ee_xyz[None, :], axis=1).min())})

    if strategy == "vlm":
        kept_labels, note = _vlm_pick_clusters(impl, cloud, labels, summaries,
                                                ee_xyz, obj, workdir)
        if not kept_labels:
            # Fallback: nearest centroid if VLM picker fails.
            kept_labels = [min(summaries, key=lambda s: s["d_centroid"])["label"]]
            note = note + " | FALLBACK to nearest-centroid"
    elif strategy == "largest":
        kept_labels = [max(summaries, key=lambda s: s["n"])["label"]]
        note = "strategy=largest"
    elif strategy == "containing_ee":
        NEAR = 0.08
        kept_labels = [s["label"] for s in summaries if s["d_min_to_ee"] <= NEAR]
        if not kept_labels:
            kept_labels = [min(summaries, key=lambda s: s["d_centroid"])["label"]]
        note = f"strategy=containing_ee (≤{NEAR}m)"
    else:  # nearest
        kept_labels = [min(summaries, key=lambda s: s["d_centroid"])["label"]]
        note = "strategy=nearest"

    kept_mask = np.isin(labels, kept_labels)
    summary_str = [(s["label"], s["n"], round(s["d_min_to_ee"], 3),
                     round(s["d_centroid"], 3)) for s in summaries]
    return cloud[kept_mask], (f"DBSCAN {len(uniq)} clusters, {note}; "
                               f"kept={kept_labels} (n={int(kept_mask.sum())}); "
                               f"all=[label,n,d_min,d_cent]={summary_str}")


def _vlm_pick_clusters(impl, cloud: np.ndarray, labels: np.ndarray,
                        summaries: list[dict], ee_xyz: np.ndarray, obj: str,
                        workdir: Path) -> tuple[list[int], str]:
    """Project each cluster's points onto a 2×2 camera panorama (different
    color per cluster + numeric label at centroid), ask VLM to reply with
    comma-separated cluster #s that ARE the {obj}. Returns (kept_label_list,
    note)."""
    import cv2, re
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image
    impl._update_render(); impl.cameras.update_picture()
    rgb_all = impl.cameras.get_rgb()
    cfg_all = impl.cameras.get_config()
    cams = ["head_camera", "front_camera", "left_camera", "right_camera"]

    # Distinct colors per cluster (BGR).
    palette = [(0,165,255), (0,255,0), (255,0,0), (255,0,255),
                (0,255,255), (255,255,0), (200,100,0), (100,0,200)]

    tile_h = tile_w = None
    tiles: dict[str, np.ndarray] = {}
    for cam in cams:
        rgb_dict = rgb_all.get(cam); cfg = cfg_all.get(cam)
        if rgb_dict is None or cfg is None:
            continue
        rgb = rgb_dict["rgb"]
        if rgb.dtype != np.uint8:
            rgb = ((rgb*255).clip(0,255).astype(np.uint8)
                   if rgb.max()<=1 else rgb.astype(np.uint8))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
        cv2.rectangle(bgr, (0,0), (170,22), (0,0,0), -1)
        cv2.putText(bgr, cam.replace("_camera","").upper(),
                    (5,17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,255,255), 2)
        ee_uv = _project_xyz(ee_xyz, cfg)
        if ee_uv and 0 <= ee_uv[0] < bgr.shape[1] and 0 <= ee_uv[1] < bgr.shape[0]:
            cv2.drawMarker(bgr, ee_uv, (0,0,255), cv2.MARKER_CROSS, 16, 2)
            cv2.putText(bgr, "GRIPPER", (ee_uv[0]+12, ee_uv[1]-8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,0,255), 1)
        for i, s in enumerate(summaries):
            color = palette[i % len(palette)]
            pts_world = cloud[labels == s["label"]]
            for xyz in pts_world[::max(1, len(pts_world)//40)]:
                uv = _project_xyz(xyz, cfg)
                if uv and 0 <= uv[0] < bgr.shape[1] and 0 <= uv[1] < bgr.shape[0]:
                    cv2.circle(bgr, uv, 2, color, -1)
            cent_uv = _project_xyz(s["centroid"], cfg)
            if cent_uv and 0 <= cent_uv[0] < bgr.shape[1] and 0 <= cent_uv[1] < bgr.shape[0]:
                cv2.circle(bgr, cent_uv, 9, color, 2)
                cv2.putText(bgr, str(s["label"]), (cent_uv[0]+10, cent_uv[1]+4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        tiles[cam] = bgr
        tile_h, tile_w = bgr.shape[:2]

    if not tiles:
        return [], "no cameras for cluster-picker"
    blank = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
    layout = [["head_camera","front_camera"], ["left_camera","right_camera"]]
    rows = [np.concatenate([tiles.get(c, blank) for c in row], axis=1) for row in layout]
    panorama = np.concatenate(rows, axis=0)
    out_path = workdir / f"cluster_pick_{len(list(workdir.glob('cluster_pick_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(out_path), panorama)

    available = [s["label"] for s in summaries]
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", DEFAULT_MODEL)
    system = (
        "You see a 2×2 grid of 4 camera views. Each colored point cluster is "
        "labeled with its integer ID at the cluster centroid (also shown as "
        "a hollow circle in that cluster's color). A red X marks the GRIPPER. "
        f"All clusters were proposed as possibly being a '{obj}'. Your job: "
        f"pick which cluster IDs ACTUALLY are (part of) the {obj} — typically "
        "this is the cluster(s) attached to or held by the gripper. EXCLUDE "
        "clusters that are clearly other objects (the tap target like a "
        "coloured block, table reflections, robot arm parts). Reply with a "
        "comma-separated list of cluster IDs (e.g. '2,5'). If only one is "
        "the tool, reply with one ID. Available IDs: " + str(available)
    )
    user = f"Which cluster IDs are the {obj}? Reply comma-separated IDs."
    raw = _call_vlm_image(model, system, user, out_path).strip()
    ids = [int(m) for m in re.findall(r"-?\d+", raw)]
    kept = [i for i in ids if i in available]
    return kept, f"vlm-pick raw={raw[:80]!r} → kept={kept} | viz={out_path.name}"


def _project_xyz(xyz, cfg) -> tuple[int, int] | None:
    K = np.asarray(cfg["intrinsic_cv"])
    extr = np.asarray(cfg["extrinsic_cv"])
    if extr.shape == (3, 4):
        eh = np.eye(4); eh[:3] = extr; extr = eh
    pw = np.array([xyz[0], xyz[1], xyz[2], 1.0])
    pc = extr @ pw
    if pc[2] <= 1e-6:
        return None
    return (int(K[0,0]*pc[0]/pc[2] + K[0,2]), int(K[1,1]*pc[1]/pc[2] + K[1,2]))


def _sam_detect(rgb, obj) -> tuple[np.ndarray, float] | None:
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    dets = detect(rgb, obj, top_k=2)
    if not dets:
        return None
    return dets[0].mask, float(dets[0].score)


def _sam_detect_multi(rgb, queries: list[str]
                       ) -> tuple[np.ndarray, list[float], dict[str, dict]] | None:
    """Run SAM for each query, UNION the masks. Returns (union_mask,
    [per_query_scores], per_query_detail). The union covers parts of the
    object that any single query misses (e.g. 'hammer' query may mask the
    dark handle but skip the yellow head; adding 'yellow object held by
    gripper' as a second query fills in the head)."""
    union: np.ndarray | None = None
    scores: list[float] = []
    detail: dict[str, dict] = {}
    for q in queries:
        sam = _sam_detect(rgb, q)
        if sam is None:
            detail[q] = {"detected": False, "n_pix": 0, "score": 0.0}
            continue
        mask, score = sam
        scores.append(score)
        detail[q] = {"detected": True, "n_pix": int(mask.sum()),
                      "score": round(float(score), 3)}
        union = mask if union is None else (union | mask)
    if union is None:
        return None
    return union, scores, detail


def _vlm_verify_mask(rgb, mask, obj, cam, workdir) -> tuple[bool, str, Path]:
    """Crop with CONTEXT (3× bbox padding) so the VLM can recognize small
    objects in scene context — bare 30×30px crops fail even for real tools
    ("clothespin / geometric shapes / bottle" misclassifications). Green-tint
    the actual mask region within the wider context. Return (ok, reason)."""
    import cv2
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image
    ys, xs = np.where(mask)
    by0, by1 = int(ys.min()), int(ys.max())
    bx0, bx1 = int(xs.min()), int(xs.max())
    # 3× bbox padding so VLM sees scene context, clamped to image bounds.
    hpad = max(int((by1 - by0) * 1.5), 40)
    wpad = max(int((bx1 - bx0) * 1.5), 40)
    y0 = max(by0 - hpad, 0); y1 = min(by1 + hpad, rgb.shape[0])
    x0 = max(bx0 - wpad, 0); x1 = min(bx1 + wpad, rgb.shape[1])
    crop = rgb[y0:y1, x0:x1].copy()
    sub_mask = mask[y0:y1, x0:x1].astype(np.uint8)
    overlay = crop.copy()
    # Draw the mask as a CONTOUR (not filled tint) so original RGB texture is
    # preserved — VLMs need to see actual color/shape, heavy green fill turns
    # a hammer into "green vertical bar" and triggers false NO.
    contours, _ = cv2.findContours(sub_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (60, 220, 60), 2)
    # Also draw the bbox so the VLM knows the region of interest at a glance.
    cv2.rectangle(overlay, (bx0 - x0, by0 - y0), (bx1 - x0, by1 - y0), (0, 200, 0), 1)
    bgr = cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR)
    out = workdir / f"verify_{cam}_{len(list(workdir.glob('verify_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(out), bgr)
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", DEFAULT_MODEL)
    system = (
        f"You see a {cam} crop with scene context. The region enclosed by "
        f"the GREEN CONTOUR/BBOX was segmented as a '{obj}' by an upstream "
        "detector. The image inside the contour is the original camera "
        f"pixels (not tinted). Verify: does it look like (any part of) a "
        f"{obj}, even a small toy one? It MAY be partially occluded by a "
        "robot gripper. Answer NO only if it is clearly a DIFFERENT object "
        "class (e.g. a coloured cube/block when query is 'hammer', a robot "
        "arm body, or bare table). When in doubt, answer YES. Reply with "
        "exactly one word: YES or NO."
    )
    user = f"Is the contoured region a {obj} (or part of one)? Reply YES or NO."
    raw = _call_vlm_image(model, system, user, out).strip()
    m = re.match(r"\s*(YES|NO)\b", raw, re.IGNORECASE)
    if m:
        return m.group(1).upper() == "YES", raw, out
    return True, f"parse_fail:{raw[:40]}", out


def _unproject_mask(depth, mask, cfg) -> np.ndarray | None:
    K = np.asarray(cfg["intrinsic_cv"])
    extr = np.asarray(cfg["extrinsic_cv"])
    if extr.shape == (3, 4):
        eh = np.eye(4); eh[:3] = extr; extr = eh
    cam2world = np.linalg.inv(extr)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
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


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
