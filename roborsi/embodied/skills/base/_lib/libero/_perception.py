"""Perception → grasp helpers for base/libero (pure vision, NO ground-truth poses).

Reuses two embodiment-neutral pieces from the RoboTwin stack:
  * the shared SAM model (via ``detect_object._load``) for point-prompted
    segmentation, and
  * the GraspGen ZMQ client (``graspgen_infer._grasps_from_cloud``).

Flow: the object is located by a PIXEL (from ``find_pixel``); a point-prompted
SAM mask under that pixel is unprojected through LIBERO's depth (the adapter now
returns it top-down, matching the camera projection) into a world-frame cloud,
z-filtered to the object; GraspGen returns 6-DoF grasps. GraspGen chooses WHERE
to grasp; execution is an OSC top-down approach to that point (the robust LIBERO
motion — matches the working ground-truth grasp path).
"""

from __future__ import annotations

import os
import re
from typing import Any

import numpy as np

_HEAD = "agentview"
_POINT_SAM: dict[str, Any] = {}
_QUERY_STOPWORDS = {
    "a",
    "an",
    "at",
    "in",
    "of",
    "on",
    "side",
    "the",
    "to",
    "visible",
}


def _fix_on() -> bool:
    """The pure-vision grasp fixes (SAM3-first localize, whole-scene mask reject +
    DBSCAN cleaning, self-correcting re-localize, object-body descend, rim
    yaw-sweep) are ON by default — verified to cut the grasp-target error from
    ~46cm to ~4cm and lift the right-object rate 25%→82%. Set
    ``ROBORSI_GRASP_FIX=0`` to fall back to the old unguarded path."""
    return os.environ.get("ROBORSI_GRASP_FIX", "1") != "0"


def remember_pixel(state, query: str, uv) -> tuple[int, int]:
    pixel = (int(uv[0]), int(uv[1]))
    cache = getattr(state, "_perception_cache", None)
    if cache is None:
        cache = {}
        setattr(state, "_perception_cache", cache)
    key = _query_key(query)
    previous = cache.get(key) or {}
    record = {
        "query": str(query),
        "pixel": pixel,
        "tokens": sorted(_query_tokens(query)),
    }
    if tuple(previous.get("pixel") or ()) == pixel and previous.get("world"):
        record["world"] = previous["world"]
    cache[key] = record
    return pixel


def recall_pixel(state, query: str) -> tuple[int, int] | None:
    record = _recall_record(state, query)
    return tuple(record["pixel"]) if record else None


def remember_world(state, u: int, v: int, world) -> None:
    cache = getattr(state, "_perception_cache", None) or {}
    pixel = (int(u), int(v))
    for record in reversed(list(cache.values())):
        if tuple(record.get("pixel") or ()) == pixel:
            record["world"] = tuple(float(value) for value in world)
            return


def recall_world(state, query: str) -> tuple[float, float, float] | None:
    record = _recall_record(state, query)
    world = record.get("world") if record else None
    return tuple(world) if world else None


def _recall_record(state, query: str) -> dict[str, Any] | None:
    cache = getattr(state, "_perception_cache", None) or {}
    key = _query_key(query)
    exact = cache.get(key)
    if exact:
        return exact

    query_tokens = _query_tokens(query)
    if not query_tokens:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for record in cache.values():
        candidate_tokens = set(record.get("tokens") or [])
        if not candidate_tokens:
            continue
        overlap = len(query_tokens & candidate_tokens) / min(
            len(query_tokens),
            len(candidate_tokens),
        )
        if overlap >= 0.6 and (best is None or overlap >= best[0]):
            best = (overlap, record)
    return best[1] if best else None


def _query_key(query: str) -> str:
    return " ".join(sorted(_query_tokens(query)))


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(query).lower())
        if token not in _QUERY_STOPWORDS
    }


def vlm_point(state, obj: str, location: str = ""):
    """Point at `obj` in the head image with the perception VLM (sonnet).

    A VLM distinguishes look-alikes (alphabet-soup can vs tomato-sauce can) that
    Grounded-DINO-tiny cannot at 256px. Returns (u, v) in head-image pixels, or
    None if the VLM can't find it. Falls back to the detector via locate_pixel at
    the call site."""
    import os

    import cv2

    from roborsi.embodied.agent_loop.config import _POINT_SYSTEM_PROMPT
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image, _parse_json
    rgb = state.env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return None
    rgb = np.asarray(rgb)
    h, w = rgb.shape[:2]
    state.workdir.mkdir(parents=True, exist_ok=True)
    path = state.workdir / "find_pixel_head.png"
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    system = _POINT_SYSTEM_PROMPT.replace("IMG_W", str(w)).replace("IMG_H", str(h))
    where = f" ({location})" if location else ""
    user = f"Find the pixel coordinates of the {obj}{where}. Point at its center."
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", "anthropic/claude-sonnet-4-6")
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    if not parsed or "u" not in parsed:
        return None
    return int(parsed["u"]), int(parsed["v"])


def locate_pixel(rgb, query: str, scale: int = 3):
    """Grounded-DINO+SAM object-centroid (u, v) on an UPSCALED frame — the
    detector fallback when the VLM pointer isn't available. LIBERO renders at
    256px and objects are small, so upscale ~3× before detection, then scale the
    centroid back. Returns (u, v) or None."""
    import cv2

    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    rgb = np.asarray(rgb)
    up = cv2.resize(rgb, (rgb.shape[1] * scale, rgb.shape[0] * scale),
                    interpolation=cv2.INTER_CUBIC)
    dets = detect(up, query, top_k=1)
    if not dets:
        return None
    c = dets[0].centroid
    return int(round(c[0] / scale)), int(round(c[1] / scale))


def zoom_localize(state, obj: str, coarse_uv, half: int = 40, upscale: int = 4,
                  camera: str = "head_camera"):
    """Coarse→fine refine: crop a window around a coarse (u,v), upscale it, and
    re-point the perception VLM at the ENLARGED crop where the object is big
    enough to localize precisely. Returns the refined (u,v) in FULL-image coords,
    or the coarse uv if refinement fails. This is the fix for LIBERO's 256px
    small-object localization: on the full frame an object is ~20px and the VLM
    points imprecisely / on a look-alike; in a 4× crop it's ~80px and crisp."""
    import os

    import cv2

    from roborsi.embodied.agent_loop.config import _POINT_SYSTEM_PROMPT
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image, _parse_json
    rgb = state.env.take_snapshot().images.get(camera)
    if rgb is None:
        return coarse_uv
    rgb = np.asarray(rgb)
    h, w = rgb.shape[:2]
    cu, cv = int(coarse_uv[0]), int(coarse_uv[1])
    x0, x1 = max(0, cu - half), min(w, cu + half)
    y0, y1 = max(0, cv - half), min(h, cv + half)
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return coarse_uv
    ch, cw = crop.shape[:2]
    big = cv2.resize(crop, (cw * upscale, ch * upscale), interpolation=cv2.INTER_CUBIC)
    state.workdir.mkdir(parents=True, exist_ok=True)
    path = state.workdir / "find_pixel_zoom.png"
    cv2.imwrite(str(path), cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
    system = _POINT_SYSTEM_PROMPT.replace("IMG_W", str(cw * upscale)).replace("IMG_H", str(ch * upscale))
    user = (f"This is a ZOOMED-IN crop of a tabletop. Point at the exact CENTER of "
            f"the {obj}. If the {obj} is not in this crop, return found:false.")
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", "anthropic/claude-sonnet-4-6")
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    if not parsed or "u" not in parsed:
        return coarse_uv                       # object not in crop / no point → keep coarse
    fu = x0 + int(parsed["u"]) / upscale
    fv = y0 + int(parsed["v"]) / upscale
    return int(round(fu)), int(round(fv))


_GDINO_BASE: dict[str, Any] = {}


def _load_gdino_base():
    """Load GroundingDINO-BASE once (stronger than the shared -tiny used by
    detect_object). Kept libero-local so RoboTwin's detector is unchanged."""
    if "mod" in _GDINO_BASE:
        return _GDINO_BASE
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    _GDINO_BASE["proc"] = AutoProcessor.from_pretrained("IDEA-Research/grounding-dino-base")
    _GDINO_BASE["mod"] = AutoModelForZeroShotObjectDetection.from_pretrained(
        "IDEA-Research/grounding-dino-base").to(dev).eval()
    _GDINO_BASE["dev"] = dev
    return _GDINO_BASE


def locate_by_candidates(_env, rgb, target: str, scale: int = 2,
                         box_thresh: float = 0.18):
    """GroundingDINO-BASE prompted only with the public target description.

    The detector must infer candidates from pixels; simulator object-name keys
    are deliberately unavailable. Returns ``(u, v)`` in native pixels or None.
    """
    import re

    import cv2
    import torch
    from PIL import Image
    query = target.strip().lower()
    if not query:
        return None
    g = _load_gdino_base()
    img = np.asarray(rgb).astype(np.uint8)
    up = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale),
                    interpolation=cv2.INTER_CUBIC)
    pil = Image.fromarray(up)
    text = query + "."
    inp = g["proc"](images=pil, text=text, return_tensors="pt").to(g["dev"])
    with torch.no_grad():
        out = g["mod"](**inp)
    res = g["proc"].post_process_grounded_object_detection(
        out, inp.input_ids, threshold=box_thresh, text_threshold=0.15,
        target_sizes=[pil.size[::-1]])[0]
    if len(res["boxes"]) == 0:
        return None
    boxes = res["boxes"].cpu().numpy()
    scores = res["scores"].cpu().numpy()
    labels = res.get("labels") or res.get("text") or [""] * len(boxes)
    tset = set(re.sub(r"[^a-z ]", "", target.lower()).split())
    best = None
    for b, sc, lab in zip(boxes, scores, labels):
        overlap = len(tset & set(str(lab).lower().split()))
        key = (overlap, float(sc))
        if best is None or key > best[0]:
            best = (key, b)
    (overlap, sc), b = best[0], best[1]
    if overlap == 0 and sc < 0.3:                 # no phrase match and weak → reject
        return None
    return int((b[0] + b[2]) / 2 / scale), int((b[1] + b[3]) / 2 / scale)


_OWLV2: dict[str, Any] = {}


def _load_owlv2():
    """OWLv2-large — strongest UNGATED open-vocab detector available (SAM3 is
    HF-gated). Loaded once, libero-local. Measured 3/7 discrimination on the
    LIBERO groceries vs 1/7 for VLM-point and 0/5 for Grounded-DINO-base."""
    if "mod" in _OWLV2:
        return _OWLV2
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    _OWLV2["proc"] = Owlv2Processor.from_pretrained("google/owlv2-large-patch14-ensemble")
    _OWLV2["mod"] = Owlv2ForObjectDetection.from_pretrained(
        "google/owlv2-large-patch14-ensemble").to(dev).eval()
    _OWLV2["dev"] = dev
    return _OWLV2


def locate_by_owlv2(_env, rgb, target: str, scale: int = 3, thresh: float = 0.05):
    """OWLv2-large prompted only with the public target description.

    Runs on the 256px head frame upscaled ``scale`` times and returns ``(u, v)``
    in native pixels, or None when the target is not detected.
    """
    import cv2
    import torch
    from PIL import Image
    query = target.strip().lower()
    if not query:
        return None
    o = _load_owlv2()
    img = np.asarray(rgb).astype(np.uint8)
    up = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale),
                    interpolation=cv2.INTER_CUBIC)
    pil = Image.fromarray(up)
    sc = scale
    texts = [[f"a photo of {query}"]]
    inp = o["proc"](text=texts, images=pil, return_tensors="pt").to(o["dev"])
    with torch.no_grad():
        out = o["mod"](**inp)
    tsz = torch.tensor([pil.size[::-1]]).to(o["dev"])
    res = o["proc"].post_process_object_detection(out, threshold=thresh, target_sizes=tsz)[0]
    boxes = res["boxes"].cpu().numpy()
    scores = res["scores"].cpu().numpy()
    labels = res["labels"].cpu().numpy()
    idxs = [i for i in range(len(labels)) if int(labels[i]) == 0]
    if not idxs:
        return None
    bi = max(idxs, key=lambda i: scores[i])
    b = boxes[bi]
    return int((b[0] + b[2]) / 2 / sc), int((b[1] + b[3]) / 2 / sc)


def locate_by_sam3(rgb, target: str, threshold: float = 0.1):
    """SAM3 promptable-concept-segmentation via the ZMQ service (runs in a
    transformers-5.7 venv, isolated from this env). Returns (u, v) of the
    highest-score box for `target`, or None if the service is off / didn't
    detect it. SAM3 discriminates LIBERO groceries better than OWLv2 at native
    256 (measured 4/7 vs 3/7) — used as the PRIMARY detector, OWLv2 as fallback.
    Gated on ROBORSI_SAM3_PORT (unset → skip, graceful fallback)."""
    import os
    import pickle
    port = int(os.environ.get("ROBORSI_SAM3_PORT", "0"))
    if not port:
        return None
    import zmq
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 20000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://localhost:{port}")
    try:
        sock.send(pickle.dumps({"image": np.asarray(rgb, dtype=np.uint8),
                                "texts": [target], "threshold": threshold}))
        resp = pickle.loads(sock.recv())
    except zmq.error.ZMQError:
        return None                     # service down → fall back to OWLv2
    finally:
        sock.close()
    if "error" in resp or not resp.get("results") or not resp["results"][0]["boxes"]:
        return None
    r = resp["results"][0]
    bi = int(np.asarray(r["scores"]).argmax())
    b = r["boxes"][bi]
    return int((b[0] + b[2]) / 2), int((b[1] + b[3]) / 2)


def _place_fix_on() -> bool:
    """The CaP-style 'retreat out of the head view before localizing the place
    target' discipline is ON by default. The old fix only lifted the held object
    STRAIGHT UP (+0.18), leaving the forearm/wrist and the object hanging between
    the agentview camera (at +x, high) and the workspace, so reflections still
    read 'arm occludes the plate'. Set ``ROBORSI_PLACE_CLEAR=0`` to disable."""
    return os.environ.get("ROBORSI_PLACE_CLEAR", "1") != "0"


def retreat_from_head_view(env, ctrl, lift: float = 0.18, back: float = 0.22,
                           clear_z: float = 0.42, z_ceiling: float = 1.15):
    """Move the held object OUT of the agentview camera's line of sight to the
    workspace, CaP-style: lift high AND slide laterally toward the robot base so
    the forearm/wrist/object stop occluding the place target (a straight-up lift
    leaves them hovering over the objects). Pure-vision-safe — uses only the
    robot's OWN base + EE pose (proprioception), never object ground truth.

    Geometry (measured, libero_object suites): the agentview camera sits at ~+x,
    high, looking back toward -x/-z; the workspace objects project to the
    upper-middle of the frame and the robot base is at -x. Retreating toward the
    base (-x) and lifting drives the arm's image projection off the near/bottom
    edge, clearing the central object region. Controlled: a single servo to a
    high, base-ward pose (the descent later is the normal hover→drop)."""
    ee, _, _ = ctrl.read_pose()
    base = np.asarray(env.robot_base_pos(), dtype=float)
    horiz = base[:2] - ee[:2]                       # EE → base, in the table plane
    n = float(np.linalg.norm(horiz))
    step = (horiz / n) * back if n > 1e-6 else np.array([-back, 0.0])
    z = min(max(float(ee[2]) + lift, clear_z), z_ceiling)   # lift, but stay BELOW the ~1.4 kinematic ceiling where the arm wedges
    ctrl.servo_to([float(ee[0]) + float(step[0]), float(ee[1]) + float(step[1]), z],
                  gripper="close", max_iters=70)


def localize_precise(state, obj: str):
    """Full localization cascade over visible RGB only.

    Optional local detectors are used when explicitly configured. The
    perception VLM is the portable path and the tiny detector is a final local
    fallback. Missing model assets must not turn one tool call into a network
    retry loop.
    """
    env = state.env
    rgb = env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return None
    uv = locate_by_sam3(rgb, obj)
    if uv is not None:
        return remember_pixel(state, obj, uv)
    uv = vlm_point(state, obj)
    if uv is not None:
        return remember_pixel(state, obj, zoom_localize(state, obj, uv))
    if os.environ.get("ROBORSI_OWLV2_ENABLE", "0") == "1":
        try:
            uv = locate_by_owlv2(env, rgb, obj)
        except Exception:
            uv = None
        if uv is not None:
            return remember_pixel(state, obj, uv)
    try:
        uv = locate_pixel(rgb, obj)
    except Exception:
        uv = None
    if uv is None:
        return None
    return remember_pixel(state, obj, zoom_localize(state, obj, uv))


def _load_point_sam():
    if "model" in _POINT_SAM:
        return _POINT_SAM["processor"], _POINT_SAM["model"]
    import torch
    from transformers import SamModel, SamProcessor

    model_name = os.environ.get("ROBORSI_POINT_SAM_MODEL", "facebook/sam-vit-base")
    local_only = (
        os.environ.get("HF_HUB_OFFLINE") == "1"
        or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    )
    processor = SamProcessor.from_pretrained(
        model_name,
        local_files_only=local_only,
    )
    model = SamModel.from_pretrained(
        model_name,
        local_files_only=local_only,
    ).to("cuda" if torch.cuda.is_available() else "cpu").eval()
    _POINT_SAM.update({"processor": processor, "model": model})
    return processor, model


def sam_mask_at_point(rgb, u: int, v: int) -> np.ndarray:
    """Highest-IoU point-prompted SAM mask under pixel (u=col, v=row).

    A point prompt (not a Grounded-DINO box) is what makes this reliable: the
    VLM's ``find_pixel`` already localized the object, so we segment exactly
    THAT object instead of letting the detector re-pick the wrong one among
    look-alikes."""
    import torch
    from PIL import Image

    sp, sm = _load_point_sam()
    inp = sp(images=Image.fromarray(np.asarray(rgb)),
             input_points=[[[int(u), int(v)]]], return_tensors="pt").to(sm.device)
    with torch.no_grad():
        out = sm(**inp)
    masks = sp.image_processor.post_process_masks(
        out.pred_masks.cpu(), inp["original_sizes"].cpu(),
        inp["reshaped_input_sizes"].cpu())[0][0].numpy()
    scores = out.iou_scores.cpu().numpy().reshape(-1)
    if _fix_on():
        return _pick_object_mask(masks, scores, int(u), int(v))
    return masks[int(scores.argmax())].astype(bool)


def _pick_object_mask(masks, scores, u: int, v: int):
    """An OBJECT-sized SAM mask covering the pointed pixel, or ``None`` to REJECT
    a whole-scene proposal. SAM's top-IoU mask is often the largest (whole-table)
    one, whose cloud centroid lands ~0.5 m off the object and wrecks GraspGen
    (verified: object masks ~700 px, table masks 7k–65k). We take the highest-IoU
    OBJECT-sized covering mask; if none, the smallest covering mask; if even that
    is a large fraction of the frame it's not an object → reject so the VLM
    re-perceives instead of grasping empty table."""
    m = np.asarray(masks).astype(bool)
    if m.ndim == 2:
        m = m[None]
    frac = m.reshape(len(m), -1).mean(axis=1)
    cover = [i for i in range(len(m)) if m[i][int(v), int(u)]]
    if not cover:
        return None
    obj = [i for i in cover if 0.0003 <= frac[i] <= 0.08]
    if obj:
        return m[max(obj, key=lambda i: scores[i])]
    smallest = min(cover, key=lambda i: frac[i])
    return m[smallest] if frac[smallest] <= 0.20 else None


def _densest_cluster(pts: np.ndarray) -> np.ndarray:
    """Keep the biggest DBSCAN cluster (the object body), dropping table/background
    skirt a slightly-loose mask let in — CaP's ``filter_noise`` idea. No-op if
    sklearn is missing or clustering finds nothing. Runs AFTER mask selection has
    already rejected whole-table masks, so the biggest cluster is the object."""
    if len(pts) < 60:
        return pts
    try:
        from sklearn.cluster import DBSCAN
    except Exception:
        return pts
    sub = pts
    if len(pts) > 4000:
        sub = pts[np.random.choice(len(pts), 4000, replace=False)]
    lab = DBSCAN(eps=0.02, min_samples=12).fit_predict(sub[:, :3])
    ids = [l for l in set(lab.tolist()) if l != -1]
    if not ids:
        return pts
    biggest = max(ids, key=lambda l: int((lab == l).sum()))
    c = sub[lab == biggest]
    lo, hi = c.min(axis=0) - 0.01, c.max(axis=0) + 0.01
    keep = pts[np.all((pts >= lo) & (pts <= hi), axis=1)]
    return keep if len(keep) >= 30 else pts


def object_cloud(env, u: int, v: int, camera: str = _HEAD, z_band: float = 0.10):
    """World-frame object cloud from a point-prompted SAM mask + LIBERO depth,
    z-filtered to the object (drops table / background points that would drag
    the GraspGen input off the object)."""
    from robosuite.utils import camera_utils as cu
    rgb = env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return None
    mask = sam_mask_at_point(rgb, u, v)
    if mask is None:                          # SAM only offered a whole-scene mask
        return None
    sim = env._env.env.sim
    h, w = env._camera_hw
    depth = np.asarray(env.depth_map(camera))
    d3 = depth if depth.ndim == 3 else depth[..., None]
    d2 = d3[..., 0]
    cam2world = np.linalg.inv(cu.get_camera_transform_matrix(sim, camera, h, w))
    rs, cs = np.where(mask & (d2 > 0))
    if len(rs) < 30:
        return None
    pts = np.asarray([
        cu.transform_from_pixels_to_world(np.array([int(r), int(c)]), d3, cam2world)
        for r, c in zip(rs, cs)
    ])
    zmed = float(np.median(pts[:, 2]))
    pts = pts[np.abs(pts[:, 2] - zmed) < z_band]
    if _fix_on():
        pts = _densest_cluster(pts)
        if len(pts) > 3500:                   # still whole-table after cleaning → reject
            return None
    if len(pts) >= 30 and os.environ.get("ROBORSI_GRASP_MV"):
        pts = _add_wrist_view(env, pts)
    return pts if len(pts) >= 30 else None


def _add_wrist_view(env, obj_pts, wrist: str = "robot0_eye_in_hand", pad: float = 0.02):
    """Fuse wrist-camera geometry within the object's world bbox (CaP-style
    multiview) so GraspGen sees more than the head camera's front shell — the
    single-view partial cloud of a bowl is a rim shell whose centroid sits inside
    the bowl, so GraspGen grasps the interior (air). Geometric crop to the object
    region — no second SAM needed. Arm/gripper points outside the small bbox are
    naturally excluded."""
    from robosuite.utils import camera_utils as cu
    sim = env._env.env.sim
    h, w = env._camera_hw
    depth = env.depth_map(wrist)
    if depth is None:
        return obj_pts
    d3 = np.asarray(depth)
    d3 = d3 if d3.ndim == 3 else d3[..., None]
    d2 = d3[..., 0]
    cam2world = np.linalg.inv(cu.get_camera_transform_matrix(sim, wrist, h, w))
    rs, cs = np.where(d2 > 0)
    if len(rs) == 0:
        return obj_pts
    if len(rs) > 2500:                                  # cap for per-grasp latency
        keep = np.random.choice(len(rs), 2500, replace=False)
        rs, cs = rs[keep], cs[keep]
    wpts = np.asarray([
        cu.transform_from_pixels_to_world(np.array([int(r), int(c)]), d3, cam2world)
        for r, c in zip(rs, cs)
    ])
    lo, hi = obj_pts.min(axis=0) - pad, obj_pts.max(axis=0) + pad
    m = np.all((wpts >= lo) & (wpts <= hi), axis=1)
    return np.concatenate([obj_pts, wpts[m]], axis=0) if m.any() else obj_pts


def grasps_at_pixel(env, u: int, v: int, top_k: int = 3):
    """Construct a grasp from the segmented object cloud under ``(u, v)``."""
    cloud = object_cloud(env, u, v)
    if cloud is None:
        return [], None
    if os.environ.get("GRASPGEN_PORT"):
        try:
            from roborsi.embodied.sim.robotwin.graspgen_infer import _grasps_from_cloud

            grasps = _grasps_from_cloud(cloud.astype(np.float32), top_k=top_k)
        except Exception:
            grasps = []
        if grasps:
            return grasps, cloud

    point = np.median(cloud, axis=0)
    return [{
        "score": 0.0,
        "translation_tcp_world": point.tolist(),
        "rotation_matrix_world": None,
        "source": "sam+depth-topdown",
    }], cloud


def graspgen_to_eef_quat(R_grasp_world):
    """Map a GraspGen 6-DoF grasp rotation (world frame) → the robosuite Franka
    eef TARGET quaternion (xyzw) that ``servo_to(quat=…)`` drives to, so a grasp
    can be executed at its FULL orientation instead of collapsed to top-down.

    GraspGen convention (docs/GRIPPER_DESCRIPTION.md): +Z = approach, +X = jaw
    (finger-closing) axis → ``R_grasp[:,2]`` is approach, ``R_grasp[:,0]`` is jaw.
    Franka (robosuite Panda) eef functional axes, CALIBRATED from the sim gripper
    geometry (leftfinger↔rightfinger for the jaw line, palm→fingertip for approach):
    approach = eef local +Z, jaw = eef local +Y. Verified end-to-end: servoing to a
    mapped 30°-tilted pose aligns the real gripper approach to 0.7° and jaw to 0.2°.
    The eef target's columns (its local axes in world) are therefore
    [jaw×approach (local X), jaw (local Y), approach (local Z)]."""
    from scipy.spatial.transform import Rotation as _R
    Rg = np.asarray(R_grasp_world, dtype=float)
    approach = Rg[:, 2]
    jaw = Rg[:, 0]
    x_axis = np.cross(jaw, approach)                     # eef local X = Y(jaw) × Z(approach)
    R_eef = np.column_stack([x_axis, jaw, approach])
    return _R.from_matrix(R_eef).as_quat()               # xyzw, world frame


def _6dof_on() -> bool:
    """Execute at GraspGen's FULL 6-DoF orientation instead of top-down. DEFAULT
    OFF: the OSC servo can't hold orientation AND hit the target position
    precisely (no IK), so the tilted insert misses-seats the object and the jaws
    close loose (gap ~0.079 ≈ near max-open) — measured lift 15% vs top-down 29%.
    The frame mapping (graspgen_to_eef_quat) is validated and kept for when a real
    IK executor lands; until then top-down is the better path. ``=1`` to force on."""
    return os.environ.get("ROBORSI_GRASP_6DOF", "0") != "0"


def _body_zmin(cloud):
    """Object-body floor (just above the lowest cloud point) so the tcp never
    drives below the object into the table, or ``None`` if no usable cloud."""
    if cloud is None or len(cloud) < 10:
        return None
    return float(cloud[:, 2].min()) + 0.005


def execute_6dof(env, grasp: dict, cloud=None, standoff: float = 0.08,
                 lift: float = 0.10):
    """Execute a GraspGen grasp at its FULL 6-DoF pose: open → pregrasp standoff
    (back off along the mapped approach) → insert along approach → close → lift.
    The wrist target quat is ``graspgen_to_eef_quat(rotation_matrix_world)`` so
    the jaws straddle the grasp width instead of always closing top-down (which
    misses bowls/side-grasp objects). The tcp z is clamped to the object body so
    the wrist never crashes through the table. Uses the servo kinematic-limit
    guard: if pregrasp or insert can't be reached, BAIL (return holds=False) so
    the caller tries the next candidate. Returns (grasp_point, final_ee_pos,
    gripper_qpos)."""
    from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
    ctrl = LiberoControl(env)
    tcp = np.asarray(grasp["translation_tcp_world"], dtype=float).copy()
    R_grasp = np.asarray(grasp["rotation_matrix_world"], dtype=float)
    q = graspgen_to_eef_quat(R_grasp)
    approach = R_grasp[:, 2]
    approach = approach / (float(np.linalg.norm(approach)) or 1.0)
    # SAFETY: never let the tcp target dip below the object body (into the table).
    zmin = _body_zmin(cloud)
    if zmin is not None and tcp[2] < zmin:
        tcp[2] = zmin
    ctrl.set_gripper(close=False)
    pre = tcp - approach * standoff                    # back off ALONG the approach
    if zmin is not None and pre[2] < zmin:
        pre[2] = zmin
    reached, _ = ctrl.servo_to([pre[0], pre[1], pre[2]], quat=q, gripper="open",
                               rot_tol=0.08)
    if not reached:
        ee, _, gq = ctrl.read_pose()                   # wedged at pregrasp → bail
        return tcp, np.asarray(ee), gq
    reached, _ = ctrl.servo_to([tcp[0], tcp[1], tcp[2]], quat=q, gripper="open",
                               rot_tol=0.08)
    if not reached:
        ee, _, gq = ctrl.read_pose()                   # wedged on insert → bail
        return tcp, np.asarray(ee), gq
    ctrl.set_gripper(close=True)
    ctrl.servo_to([tcp[0], tcp[1], tcp[2] + lift], quat=q, gripper="close",
                  rot_tol=0.08)                         # LIFT to test the hold
    ee, _, gq = ctrl.read_pose()
    return tcp, np.asarray(ee), gq


def execute_topdown(
    env,
    grasp: dict,
    cloud=None,
    hover: float = 0.10,
    yaw: float = 0.0,
    z_offset: float = 0.0,
):
    """Grasp at GraspGen's chosen point. With ``ROBORSI_GRASP_6DOF`` on (default)
    and a grasp rotation available AND no explicit ``yaw`` override, execute the
    FULL 6-DoF pose (``execute_6dof``) so bowls/side-grasp objects get grasped;
    otherwise fall back to the OSC top-down approach (open → hover → descend →
    close → lift). The descend height is clamped to the object's body (from the
    cloud) so a too-low GraspGen point can't drive the fingers into the table.
    ``yaw`` rotates the gripper about world +Z so the jaws can straddle a thin rim
    wall (bowls) instead of closing along it — forcing ``yaw`` also forces the
    top-down path (the caller's rim yaw-sweep retry). Returns (grasp_point,
    final_ee_pos, gripper_qpos)."""
    if _6dof_on() and not yaw and grasp.get("rotation_matrix_world") is not None:
        # Only take the 6-DoF path for GENUINELY TILTED grasps. Applying it to
        # every grasp regressed lift (29%→15%): near-vertical grasps that top-down
        # already handles got a looser, oriented grip. So keep top-down for
        # near-vertical (approach within 25° of straight-down) and reserve 6-DoF
        # for the side/tilted grasps top-down can't do (bowls, side-graspable).
        _appr = np.asarray(grasp["rotation_matrix_world"], dtype=float)[:, 2]
        _appr = _appr / (float(np.linalg.norm(_appr)) or 1.0)
        _tilt = np.degrees(np.arccos(np.clip(-_appr[2], -1.0, 1.0)))   # angle off straight-down
        if _tilt > 25.0:
            return execute_6dof(env, grasp, cloud=cloud, lift=hover)
    from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
    ctrl = LiberoControl(env)
    p = np.asarray(grasp["translation_tcp_world"], dtype=float).copy()
    if cloud is not None and len(cloud) >= 10:
        zmed, zmax = float(np.median(cloud[:, 2])), float(cloud[:, 2].max())
        if _fix_on():
            zmin = float(cloud[:, 2].min())
            # Descend into the object BODY (≤ median), not the rim/top: the old
            # clamp to zmax closed the fingers on air even on ACCURATE grasps
            # (verified: 2cm-accurate grasp, +3cm-high descend → gap 0.002). Floor
            # just above the lowest object point so we never drive into the table.
            p[2] = float(np.clip(p[2], zmin + 0.005, zmed))
        else:
            p[2] = float(np.clip(p[2], zmed - 0.01, zmax))   # keep the grasp on the object body
    p[2] += float(z_offset)
    if cloud is not None and len(cloud) >= 10:
        p[2] = max(p[2], float(np.min(cloud[:, 2])) + 0.001)
    q = None
    if yaw:
        import robosuite.utils.transform_utils as T
        _, cur_q, _ = ctrl.read_pose()
        qz = T.axisangle2quat(np.array([0.0, 0.0, float(yaw)]))
        q = T.quat_multiply(qz, cur_q)          # yaw the gripper about world +Z
    ctrl.set_gripper(close=False)
    ctrl.servo_to([p[0], p[1], p[2] + hover], quat=q, gripper="open")
    ctrl.servo_to([p[0], p[1], p[2]], quat=q, gripper="open")
    ctrl.set_gripper(close=True)
    ctrl.servo_to([p[0], p[1], p[2] + hover], quat=q, gripper="close")
    ee, _, gq = ctrl.read_pose()
    return p, np.asarray(ee), gq
