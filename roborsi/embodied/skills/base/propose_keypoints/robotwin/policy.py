"""base.robotwin.propose_keypoints — ReKep semantic keypoint proposer.

This skill IS the real ReKep keypoint-proposal implementation (NOT a thin
wrapper). For a SAM-masked object it extracts DINOv2 patch features inside
the mask and clusters them with k-means(cosine) → k cluster centroids land
on anatomically meaningful regions (head vs handle, strike face vs claw,
bottle neck vs body). Use centroids as candidate keypoints for VLM picking
— much better than uniform-stride sampling.

Reference: ReKep paper (Huang et al., CoRL 2024), Sec 3.4 keypoint proposal.

The DINOv2 model is loaded lazily once per process and held module-level.
SAM grounding reuses `detect` from the detect_object skill (the perception
core). robotwin_agent is imported lazily inside dispatch_runtime only, to
avoid a circular import.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect


_MODEL_CACHE: dict = {}


def _load_dinov2():
    if "model" in _MODEL_CACHE:
        return _MODEL_CACHE["model"], _MODEL_CACHE["processor"]
    from transformers import AutoImageProcessor, AutoModel
    import torch
    # facebook/dinov2-small is ~22M params, faster than -base/-large with
    # very similar feature quality for semantic clustering at this scale.
    model_name = "facebook/dinov2-small"
    processor = AutoImageProcessor.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).eval()
    if torch.cuda.is_available():
        model = model.cuda()
    _MODEL_CACHE["model"] = model
    _MODEL_CACHE["processor"] = processor
    _MODEL_CACHE["device"] = "cuda" if torch.cuda.is_available() else "cpu"
    return model, processor


def dinov2_patch_features(rgb: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """Return (patch_features, (grid_h, grid_w)). patch_features shape =
    (grid_h * grid_w, feature_dim). RGB uint8 HxWx3."""
    import torch
    model, processor = _load_dinov2()
    device = _MODEL_CACHE["device"]
    inputs = processor(images=rgb, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    # outputs.last_hidden_state: (1, 1 + grid_h*grid_w, dim). First token = CLS.
    feats = outputs.last_hidden_state[0, 1:, :].cpu().numpy()
    # Default patch size is 14, image processed to 224x224 → grid 16x16 = 256.
    # Sanity check:
    grid_side = int(np.sqrt(feats.shape[0]))
    return feats, (grid_side, grid_side)


def propose_semantic_keypoints(rgb: np.ndarray, mask: np.ndarray, k: int = 5,
                                 min_pixel_separation: int = 8
                                 ) -> list[tuple[int, int]]:
    """Cluster DINOv2 features inside SAM mask with k-means(cosine), return
    k cluster centroid pixels. ReKep-style.

    Args:
      rgb:   uint8 HxWx3 image
      mask:  HxW bool mask of the object
      k:     number of cluster centroids
      min_pixel_separation: drop centroids closer than this to a stronger one
    Returns:
      list of (u, v) pixel coords inside the mask, k or fewer if filtered.
    """
    H, W = mask.shape
    if mask.sum() < k:
        return []
    feats, (gh, gw) = dinov2_patch_features(rgb)
    # Bilinear upsample feature map to (H, W) per ReKep.
    feats_grid = feats.reshape(gh, gw, -1)
    import torch
    import torch.nn.functional as F
    fg = torch.from_numpy(feats_grid).permute(2, 0, 1).unsqueeze(0)  # (1, D, gh, gw)
    fu = F.interpolate(fg, size=(H, W), mode="bilinear", align_corners=False)
    fu = fu[0].permute(1, 2, 0).numpy()  # (H, W, D)
    # Pick masked pixel features.
    ys, xs = np.where(mask)
    pix_feats = fu[ys, xs]
    # L2-normalize for cosine k-means.
    norms = np.linalg.norm(pix_feats, axis=1, keepdims=True)
    pix_feats = pix_feats / np.maximum(norms, 1e-8)
    # k-means with cosine metric == k-means on L2-normalized vectors.
    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=min(k, len(ys)), n_init=3, random_state=0)
    labels = km.fit_predict(pix_feats)
    # For each cluster, take centroid in PIXEL space (mean of member coords).
    candidates: list[tuple[int, int, int]] = []  # (u, v, n_members)
    for c in range(km.n_clusters):
        members = labels == c
        if members.sum() == 0:
            continue
        cy = float(np.mean(ys[members]))
        cx = float(np.mean(xs[members]))
        candidates.append((int(round(cx)), int(round(cy)), int(members.sum())))
    # Sort by cluster size desc; filter close pairs.
    candidates.sort(key=lambda c: -c[2])
    out: list[tuple[int, int]] = []
    for u, v, _ in candidates:
        if any((u - u2) ** 2 + (v - v2) ** 2 < min_pixel_separation ** 2
               for u2, v2 in out):
            continue
        out.append((u, v))
    return out


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    obj = (args.get("object") or "").strip()
    if not obj:
        return ({"ok": False, "reason": "object (str) required"},
                _snapshot(state.env))
    k = int(args.get("k", 5))
    cam = str(args.get("camera", "head_camera"))
    min_sep = int(args.get("min_pixel_separation", 8))

    impl = state.env._impl
    impl._update_render(); impl.cameras.update_picture()
    rgb_dict = impl.cameras.get_rgb().get(cam)
    if rgb_dict is None:
        return ({"ok": False, "reason": f"camera {cam!r} not available"},
                _snapshot(state.env))
    rgb = rgb_dict["rgb"]
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
               if rgb.max() <= 1 else rgb.astype(np.uint8))
    dets = detect(rgb, obj, top_k=2)
    if not dets:
        return ({"ok": False, "reason": f"SAM did not detect {obj!r} on {cam}"},
                _snapshot(state.env))
    mask = dets[0].mask
    centroids = propose_semantic_keypoints(rgb, mask, k=k,
                                             min_pixel_separation=min_sep)
    if not centroids:
        return ({"ok": False,
                 "reason": f"mask too small or k too large; got 0 centroids"},
                _snapshot(state.env))
    return ({"ok": True,
             "keypoints_uv": [[int(u), int(v)] for u, v in centroids],
             "camera": cam,
             "n_clusters_returned": len(centroids),
             "sam_score": round(float(dets[0].score), 3),
             "note": ("ReKep-style DINOv2 k-means(cosine) cluster centroids "
                      "inside the SAM mask. Each centroid lands on a "
                      "semantically distinct sub-region. Use with "
                      "label_points_grid or unproject_pixel for fine "
                      "Set-of-Mark picking.")},
            _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
