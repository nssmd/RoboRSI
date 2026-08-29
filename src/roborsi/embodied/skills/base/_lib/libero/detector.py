"""Grounding-DINO plus SAM object grounding shared by LIBERO skills.

This skill IS the real perception core (NOT a thin wrapper). It lazy-loads
Grounding-DINO + SAM once per process and exposes the module-level
`detect(image_rgb, query)` pure function (numpy in → list[Detection] out),
plus a per-frame detect cache. NO VLM IN THE LOOP — fully deterministic.

Each Detection has:
  bbox:    (x0, y0, x1, y1) ints in image coords
  mask:    (H, W) bool ndarray
  score:   float, Grounding-DINO confidence
  centroid:(u, v) ints — mask centroid (x, y)

This is what the Rollout paper calls the "object center point" tier
(Appendix A). Every find_pixel / get_object_bbox /
segment_object_pointcloud / multi_view_fusion / propose_keypoints call
routes through `detect` here, so the whole sim perception stack lives
inside the self-evolving skill closure.

`detect()` is a pure function and does not read simulator state. Only
`dispatch_runtime` touches the generic rollout interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
from PIL import Image


_MODELS: dict[str, Any] = {}
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ── per-frame detect cache ────────────────────────────────────────────
# Multi-step executor and many composite skills call find_pixel /
# detect_object / segment_object_pointcloud multiple times on the same
# camera frame within one sub-step. Re-running Grounding-DINO + SAM on
# an identical (image, query, params) tuple is wasted work. Cache here.
_DETECT_CACHE: dict[tuple, list["Detection"]] = {}
_DETECT_CACHE_MAX = 128
_DETECT_CACHE_STATS = {"hits": 0, "misses": 0}


def _image_key(image_rgb: np.ndarray) -> bytes:
    """Fast 8-byte digest of an image. uint8 ndarrays hash via tobytes."""
    import hashlib
    arr = image_rgb
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    return hashlib.blake2b(arr.tobytes(), digest_size=8).digest()


def cache_stats() -> dict[str, int]:
    return {**_DETECT_CACHE_STATS, "size": len(_DETECT_CACHE)}


def clear_cache() -> None:
    """Drop all cached detections (call between unrelated sim episodes)."""
    _DETECT_CACHE.clear()
    _DETECT_CACHE_STATS["hits"] = 0
    _DETECT_CACHE_STATS["misses"] = 0


def _load() -> tuple[Any, Any, Any, Any]:
    if "loaded" in _MODELS:
        return _MODELS["gdp"], _MODELS["gdm"], _MODELS["sp"], _MODELS["sm"]
    from transformers import (AutoProcessor, AutoModelForZeroShotObjectDetection,
                              SamProcessor, SamModel)
    gdp = AutoProcessor.from_pretrained("IDEA-Research/grounding-dino-tiny")
    gdm = AutoModelForZeroShotObjectDetection.from_pretrained(
        "IDEA-Research/grounding-dino-tiny").to(_DEVICE).eval()
    sp = SamProcessor.from_pretrained("facebook/sam-vit-base")
    sm = SamModel.from_pretrained("facebook/sam-vit-base").to(_DEVICE).eval()
    _MODELS.update({"gdp": gdp, "gdm": gdm, "sp": sp, "sm": sm, "loaded": True})
    return gdp, gdm, sp, sm


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1
    mask: np.ndarray                  # (H, W) bool
    score: float
    centroid: tuple[int, int]         # (u, v) = (x, y) mask centroid


@torch.no_grad()
def detect(image_rgb: np.ndarray, query: str, *,
           box_threshold: float = 0.25,
           text_threshold: float = 0.20,
           top_k: int = 5) -> list[Detection]:
    """Detect all objects matching `query`, return up to `top_k` ranked by score."""
    if image_rgb.dtype != np.uint8:
        image_rgb = ((image_rgb * 255).clip(0, 255).astype(np.uint8)
                     if image_rgb.max() <= 1 else image_rgb.astype(np.uint8))
    # ── cache lookup (same frame + same query + same params) ──
    cache_key = (_image_key(image_rgb), query.lower().strip(),
                   float(box_threshold), float(text_threshold), int(top_k))
    cached = _DETECT_CACHE.get(cache_key)
    if cached is not None:
        _DETECT_CACHE_STATS["hits"] += 1
        return cached
    _DETECT_CACHE_STATS["misses"] += 1
    pil = Image.fromarray(image_rgb)
    gdp, gdm, sp, sm = _load()
    inp = gdp(images=pil, text=query.lower().strip() + ".",
              return_tensors="pt").to(_DEVICE)
    out = gdm(**inp)
    res = gdp.post_process_grounded_object_detection(
        out, inp.input_ids, threshold=box_threshold,
        text_threshold=text_threshold, target_sizes=[pil.size[::-1]])[0]
    if len(res["boxes"]) == 0:
        return []
    boxes = res["boxes"].cpu().numpy()
    scores = res["scores"].cpu().numpy()
    order = np.argsort(-scores)[:top_k]
    boxes = boxes[order]; scores = scores[order]

    sam_in = sp(images=pil, input_boxes=[boxes.tolist()],
                return_tensors="pt").to(_DEVICE)
    sam_out = sm(**sam_in)
    masks = sp.image_processor.post_process_masks(
        sam_out.pred_masks.cpu(), sam_in["original_sizes"].cpu(),
        sam_in["reshaped_input_sizes"].cpu())[0]

    out_dets: list[Detection] = []
    for i, (b, sc) in enumerate(zip(boxes, scores)):
        m = masks[i][0].numpy().astype(bool)
        ys, xs = np.where(m)
        if len(xs):
            cu, cv = int(round(xs.mean())), int(round(ys.mean()))
        else:
            cu, cv = int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2)
        out_dets.append(Detection(
            bbox=tuple(int(round(x)) for x in b),  # type: ignore
            mask=m, score=float(sc), centroid=(cu, cv)))
    # ── cache store (bounded; drop oldest on overflow) ──
    if len(_DETECT_CACHE) >= _DETECT_CACHE_MAX:
        _DETECT_CACHE.pop(next(iter(_DETECT_CACHE)))
    _DETECT_CACHE[cache_key] = out_dets
    return out_dets


def best(image_rgb: np.ndarray, query: str,
         **kwargs: Any) -> Detection | None:
    dets = detect(image_rgb, query, **kwargs)
    return dets[0] if dets else None


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    obj = str(args.get("object", "")).strip()
    if not obj:
        return ({"ok": False, "reason": "object name required"}, _snapshot(state.env))
    top_k = int(args.get("top_k", 5))
    box_thr = float(args.get("box_threshold", 0.25))
    text_thr = float(args.get("text_threshold", 0.20))

    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is None:
        return ({"ok": False, "reason": "no head_camera"}, obs)
    head_arr = np.asarray(head)
    dets = detect(head_arr, obj, top_k=top_k,
                  box_threshold=box_thr, text_threshold=text_thr)
    if not dets:
        return ({"ok": False, "reason": f"no detections for '{obj}'",
                 "hint": "use a more concrete noun phrase or lower box_threshold"},
                obs)

    # Whole-frame demotion. Grounding-DINO answers EVERY query with something,
    # and when the named object is absent, small, or low-contrast the thing it
    # returns is a box spanning the entire frame — the table itself. Measured on
    # stamp_seal: "flat colored square marker on table" returned bbox
    # [0,18,320,240] at score 0.438 as the TOP detection, ranked ABOVE the real
    # 39x33-px pad at 0.426; the same frame-filling box appeared in 4/4 seeds.
    # Unprojecting its centroid yields a confident world XYZ in the middle of
    # the table, and the arm gets driven there. The sibling mask path already
    # refuses this (_perception._WHOLE_SCENE_MASK_FRAC = 0.40), but this tool did
    # not, so the bad box reached callers through the cheaper route.
    #
    # Demote rather than drop: a frame-filling box IS the right answer to "the
    # table", so keep it, rank it below every plausible object box, and label it.
    # `best` becomes a real object whenever one was found; `detections` still
    # lists everything, so a consumer that re-sorts by its own rule is unaffected.
    whole_frame_frac = 0.40
    frame_area = float(head_arr.shape[0] * head_arr.shape[1])
    out_list = []
    for d in dets:
        x0, y0, x1, y1 = d.bbox
        frac = ((max(0, x1 - x0) * max(0, y1 - y0)) / frame_area
                if frame_area else 0.0)
        entry = {"bbox": list(d.bbox), "centroid": list(d.centroid),
                 "score": round(d.score, 3), "bbox_frame_frac": round(frac, 3)}
        if frac > whole_frame_frac:
            entry["whole_frame"] = True
            entry["note"] = (f"bbox covers {frac:.0%} of the frame — this is the "
                             f"table/background, not '{obj}'. Ranked last; do "
                             f"not unproject its centroid.")
        out_list.append(entry)
    out_list.sort(key=lambda e: (bool(e.get("whole_frame")), -e["score"]))
    result = {"ok": True, "detections": out_list, "best": out_list[0],
              "n": len(out_list)}
    if out_list[0].get("whole_frame"):
        result["all_whole_frame"] = True
        result["hint"] = (f"EVERY detection for '{obj}' fills the frame — it was "
                          f"almost certainly NOT found. Re-query with a different "
                          f"noun phrase; do not use this centroid.")
    return (result, obs)


def run(env=None, **kwargs: Any):
    raise NotImplementedError("Call via rollout tool dispatch.")
