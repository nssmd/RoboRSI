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


def _query_tokens(query: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(query).lower())
        if token not in _QUERY_STOPWORDS
    }


def _query_key(query: str) -> str:
    return " ".join(sorted(_query_tokens(query)))


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


def _recall_record(state, query: str) -> dict[str, Any] | None:
    cache = getattr(state, "_perception_cache", None) or {}
    exact = cache.get(_query_key(query))
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
            best = overlap, record
    return best[1] if best else None


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


def write_image_atomic(path, image) -> None:
    from pathlib import Path

    import cv2

    out = Path(path)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp.png")
    if not cv2.imwrite(str(tmp), image):
        raise OSError(f"cv2.imwrite failed: {tmp}")
    os.replace(tmp, out)


def _fix_on() -> bool:
    """The pure-vision grasp fixes (SAM3-first localize, whole-scene mask reject +
    DBSCAN cleaning, self-correcting re-localize, object-body descend, rim
    yaw-sweep) are ON by default — verified to cut the grasp-target error from
    ~46cm to ~4cm and lift the right-object rate 25%→82%. Set
    ``ROBORSI_GRASP_FIX=0`` to fall back to the old unguarded path."""
    return os.environ.get("ROBORSI_GRASP_FIX", "1") != "0"


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
    # Upscale before pointing (see local_vlm_point): a VLM reasons about spatial
    # relations far better on a larger image; LIBERO's 256px is too small. Scale
    # the returned pixel back to native.
    scale = int(os.environ.get("ROBORSI_POINT_UPSCALE", "3"))
    if scale > 1:
        rgb = cv2.resize(rgb, (rgb.shape[1] * scale, rgb.shape[0] * scale),
                         interpolation=cv2.INTER_CUBIC)
    h, w = rgb.shape[:2]
    state.workdir.mkdir(parents=True, exist_ok=True)
    path = state.workdir / "find_pixel_head.png"
    write_image_atomic(path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    system = _POINT_SYSTEM_PROMPT.replace("IMG_W", str(w)).replace("IMG_H", str(h))
    where = f" ({location})" if location else ""
    user = f"Find the pixel coordinates of the {obj}{where}. Point at its center."
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", "anthropic/claude-sonnet-4-6")
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    if not parsed or "u" not in parsed:
        return None
    return int(int(parsed["u"]) / scale), int(int(parsed["v"]) / scale)


def _semantic_point_query(obj: str) -> str:
    text = str(obj or "").strip()
    words = {
        word.strip(".,;:()[]{}").lower()
        for word in text.split()
    }
    if {"cookie", "box"} <= words:
        return (
            f"{text}; preserve every spatial relationship in the phrase; "
            "the cookie box is a small low rectangular food package on the "
            "tabletop, not a tall cabinet, drawer unit, appliance, or shelf; "
            "choose the bowl physically supported by that small package"
        )
    if "box" in words:
        return (
            f"{text}; choose the small low rectangular box-shaped package, "
            "not the tall carton, bottle, cylindrical can, or basket"
        )
    if "can" in words:
        return (
            f"{text}; choose the cylindrical can matching the exact product, "
            "not a bottle, carton, box, or basket"
        )
    if "bowl" in words:
        return (
            f"{text}; preserve every spatial relationship in the phrase and "
            "choose the matching open bowl, not a plate or another bowl"
        )
    if "drawer" in words and words.intersection({"handle", "knob", "pull"}):
        return (
            f"{text}; choose the attached requested drawer handle or knob, "
            "not the cabinet panel, shelf, or a loose object"
        )
    return text


def _requires_semantic_pointing(obj: str) -> bool:
    text = " ".join(str(obj or "").lower().split())
    words = {
        word.strip(".,;:()[]{}")
        for word in text.split()
    }
    relation_words = {
        "between",
        "bottom",
        "drawer",
        "inside",
        "layer",
        "left",
        "middle",
        "next",
        "right",
        "top",
    }
    if words & relation_words:
        return True
    product_nouns = {"bottle", "box", "can", "carton", "package"}
    if words & product_nouns and len(words) >= 2:
        return True
    if words & {"bowl", "mug"} and len(words) >= 3:
        return True
    object_classes = {
        "basket",
        "block",
        "bottle",
        "bowl",
        "box",
        "burner",
        "cabinet",
        "can",
        "carton",
        "container",
        "cup",
        "door",
        "drawer",
        "handle",
        "item",
        "lid",
        "microwave",
        "mug",
        "object",
        "package",
        "pad",
        "pan",
        "plate",
        "pot",
        "rack",
        "ramekin",
        "scale",
        "shelf",
        "stand",
        "stove",
        "table",
        "thing",
        "tray",
    }
    if words and not words.intersection(object_classes):
        return True
    return False


def local_vlm_point(state, obj: str):
    """Point at `obj` via the LOCAL VLM pointing service (ZMQ, Qwen2.5-VL). The VLM
    reads the full referring expression ("the bowl between the plate and the
    ramekin") and grounds it to a box whose center we take, so it disambiguates
    look-alikes far better than a text-prompt detector — and runs on-GPU locally
    instead of loading the Claude tunnel. (Molmo was evaluated too but its image
    processor hard-requires TensorFlow, which destabilizes our env; Qwen matches
    OWLv2 accuracy — 4/6 vs 5/6, both 2.2cm — with no TF, so it is the pointer.)
    Returns (u,v) or None (service off / couldn't point)."""
    port = int(os.environ.get("ROBORSI_VLM_PORT", "0"))
    if not port:
        return None
    rgb = state.env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return None
    # Feed the pointer an UPSCALED frame, then map the point back to native pixels.
    # LIBERO renders 256px; a VLM pointer reasons about spatial relations ("between
    # the plate and the ramekin") far better on a larger image — verified: Qwen went
    # 0/3 → 2/3 correct-bowl at 3× upscale. (OWLv2 already upscales 3×; the pointers
    # did not — a resolution asymmetry that starved them.) Native >256 renders crash
    # the offscreen GL context, so use the stable interpolated upscale.
    import cv2
    rgb = np.asarray(rgb)
    scale = int(os.environ.get("ROBORSI_POINT_UPSCALE", "3"))
    img = (cv2.resize(rgb, (rgb.shape[1] * scale, rgb.shape[0] * scale),
                      interpolation=cv2.INTER_CUBIC) if scale > 1 else rgb)
    import pickle

    import zmq
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 40000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://localhost:{port}")
    try:
        sock.send(pickle.dumps({"image": img.astype(np.uint8), "text": obj}))
        uv = pickle.loads(sock.recv())
    except zmq.ZMQError:
        uv = None
    finally:
        sock.close()
    if not (isinstance(uv, (tuple, list)) and len(uv) == 2):
        return None                                  # None or a desynced/garbled reply
    return int(uv[0] / scale), int(uv[1] / scale)   # back to native pixels


def _ranked_localization_candidates(rgb, obj: str, pointer_uv=None):
    import cv2

    image = np.asarray(rgb)
    height, width = image.shape[:2]
    rows: list[dict[str, Any]] = []

    def add(u, v, bbox, source, score=None):
        u = int(round(float(u)))
        v = int(round(float(v)))
        if not (0 <= u < width and 0 <= v < height):
            return
        if any(
            float(np.linalg.norm(np.array([u, v]) - np.array([row["u"], row["v"]]))) < 12.0
            for row in rows
        ):
            return
        row = {
            "u": u,
            "v": v,
            "bbox": [int(value) for value in bbox],
            "source": str(source),
        }
        if score is not None:
            row["confidence"] = round(float(score), 3)
        rows.append(row)

    pointer_values = []
    if pointer_uv is not None:
        if (
            isinstance(pointer_uv, (list, tuple))
            and len(pointer_uv) == 2
            and all(
                isinstance(value, (int, float, np.integer, np.floating))
                for value in pointer_uv
            )
        ):
            pointer_values = [pointer_uv]
        else:
            pointer_values = list(pointer_uv)
    for pointer in pointer_values:
        if not isinstance(pointer, (list, tuple)) or len(pointer) != 2:
            continue
        u, v = int(pointer[0]), int(pointer[1])
        half = 12
        add(
            u,
            v,
            [
                max(0, u - half),
                max(0, v - half),
                min(width - 1, u + half),
                min(height - 1, v + half),
            ],
            "pointer",
        )

    query_words = {
        word.strip(".,;:()[]{}").lower()
        for word in str(obj or "").split()
    }
    queries = [str(obj or "").strip()]
    shape_words = {
        "basket",
        "bottle",
        "bowl",
        "box",
        "can",
        "carton",
        "handle",
        "knob",
        "mug",
        "package",
        "plate",
    }
    queries.extend(sorted(query_words & shape_words))
    if "box" in query_words:
        queries.append("package")
    if (
        "bowl" in query_words
        and query_words.intersection(
            {"between", "drawer", "layer", "on", "top"}
        )
    ):
        queries.append("dish")
    queries = list(dict.fromkeys(query for query in queries if query))

    try:
        from roborsi.embodied.skills.base.detect_object.robotwin.policy import (
            detect,
        )

        scale = int(os.environ.get("ROBORSI_POINT_UPSCALE", "3"))
        up = (
            cv2.resize(
                image,
                (width * scale, height * scale),
                interpolation=cv2.INTER_CUBIC,
            )
            if scale > 1
            else image
        )
        for query in queries:
            top_k = 8 if query == "dish" else 3
            for det in detect(up, query, top_k=top_k):
                bbox = [float(value) / scale for value in det.bbox]
                add(
                    float(det.centroid[0]) / scale,
                    float(det.centroid[1]) / scale,
                    bbox,
                    f"detector:{query}",
                    det.score,
                )
    except Exception:  # noqa: BLE001
        pass
    return rows


def _choose_localization_candidate(
    state,
    obj: str,
    rgb,
    candidates,
):
    if len(candidates) < 2:
        if not candidates:
            return None
        source = str(candidates[0].get("source") or "")
        if not source.startswith("pointer"):
            return None
        return int(candidates[0]["u"]), int(candidates[0]["v"])

    import cv2

    from roborsi.embodied.agent_loop.vlm_io import (
        _call_vlm_image,
        _parse_json,
    )

    image = np.asarray(rgb).copy()
    scale = int(
        os.environ.get("ROBORSI_CANDIDATE_UPSCALE", "3")
    )
    if scale > 1:
        image = cv2.resize(
            image,
            (image.shape[1] * scale, image.shape[0] * scale),
            interpolation=cv2.INTER_CUBIC,
        )
    for index, candidate in enumerate(candidates):
        x1, y1, x2, y2 = [
            int(round(float(value) * scale))
            for value in candidate["bbox"]
        ]
        color = (255, 80 + (index * 50) % 160, 40 + (index * 80) % 200)
        cv2.rectangle(
            image,
            (x1, y1),
            (x2, y2),
            color,
            max(2, scale),
        )
        cv2.putText(
            image,
            str(index),
            (max(0, x1), max(12 * scale, y1 - 4 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55 * scale,
            color,
            max(2, scale),
            cv2.LINE_AA,
        )
    from pathlib import Path

    workdir = Path(
        getattr(
            state,
            "workdir",
            "/tmp/roborsi-localization-candidates",
        )
    )
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "localization_candidates.png"
    write_image_atomic(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    system = (
        "Select the single numbered box that matches the exact target phrase "
        "using only the current image. Respect package identity and spatial "
        "relationships such as in/on/inside/next-to. Similar categories are "
        "not interchangeable. Return one JSON object only: "
        '{"index": <zero-based integer or null>, "confidence": <0-1>, '
        '"reason": "<short>"}.'
    )
    summary = [
        {
            "index": index,
            "source": row.get("source"),
            "center": [row.get("u"), row.get("v")],
            "bbox": row.get("bbox"),
        }
        for index, row in enumerate(candidates)
    ]
    user = (
        f"Target: {obj}\n"
        f"Candidates: {summary}\n"
        "Choose only if the visible identity and relationship match."
    )
    model = os.environ.get(
        "ROBORSI_PERCEPTION_MODEL",
        "anthropic/claude-sonnet-4-6",
    )
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    if not parsed:
        return None
    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError, OverflowError):
        return None
    min_confidence = float(
        os.environ.get(
            "ROBORSI_CANDIDATE_MIN_CONFIDENCE",
            "0.7",
        )
    )
    if not np.isfinite(confidence) or confidence < min_confidence:
        return None
    index = parsed.get("index")
    if isinstance(index, bool):
        return None
    try:
        index = int(index)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (0 <= index < len(candidates)):
        return None
    selected = candidates[index]
    return int(selected["u"]), int(selected["v"])


def _choose_depth_relation_candidate(
    state,
    obj: str,
    candidates,
):
    text = " ".join(str(obj or "").lower().split())
    if "bowl" not in text:
        return None
    on_top = (
        " on top of " in f" {text} "
        or " on the top of " in f" {text} "
    )
    in_top_compartment = (
        " in the top layer " in f" {text} "
        or " in top layer " in f" {text} "
        or " in the top drawer " in f" {text} "
        or " inside the top drawer " in f" {text} "
    )
    if not on_top and not in_top_compartment:
        return None
    support_query = "wooden cabinet"
    if on_top:
        for marker in (" on top of ", " on the top of "):
            if marker in f" {text} ":
                support_query = text.split(marker, 1)[1]
                break
    elif " of " in text:
        support_query = text.rsplit(" of ", 1)[1]
    support_query = support_query.strip()
    if support_query.startswith("the "):
        support_query = support_query[4:].strip()
    if not support_query:
        return None
    try:
        from roborsi.embodied.skills.base.detect_object.robotwin.policy import (
            detect,
        )

        image = np.asarray(
            state.env.take_snapshot().images.get("head_camera")
        )
        if image.ndim != 3:
            return None
        if "cookie box" in support_query:
            special = _choose_cookie_box_bowl_candidate(
                state,
                image,
                candidates,
                detect=detect,
            )
            if special is not None:
                return special
        support_detections = detect(
            image,
            support_query,
            top_k=3,
        )
        if not support_detections:
            return None
        height, width = image.shape[:2]
        support_boxes = []
        for detection in support_detections:
            x1, y1, x2, y2 = [
                float(value)
                for value in detection.bbox
            ]
            if (
                x2 <= x1
                or y2 <= y1
                or (x2 - x1) * (y2 - y1)
                > 0.90 * width * height
            ):
                continue
            support_boxes.append((x1, y1, x2, y2))
        if not support_boxes:
            return None
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for candidate in candidates:
        u = int(candidate["u"])
        v = int(candidate["v"])
        if not any(
            x1 - 12 <= u <= x2 + 12
            and y1 - 30 <= v <= y2 + 20
            for x1, y1, x2, y2 in support_boxes
        ):
            continue
        try:
            point = state.env.pixel_to_world(
                u,
                v,
            )
            point = np.asarray(point, dtype=float)
        except Exception:  # noqa: BLE001
            continue
        if point.shape != (3,) or not np.all(np.isfinite(point)):
            continue
        rows.append((float(point[2]), candidate))
    if not rows:
        return None
    rows.sort(key=lambda row: row[0], reverse=True)
    if on_top:
        selected = rows[0][1]
        return int(selected["u"]), int(selected["v"])
    if len(rows) < 2:
        return None
    highest_z = rows[0][0]
    interior = [
        row
        for row in rows[1:]
        if 0.025 <= highest_z - row[0] <= 0.30
    ]
    if not interior:
        return None
    selected = interior[0][1]
    return int(selected["u"]), int(selected["v"])


def _choose_cookie_box_bowl_candidate(
    state,
    image,
    candidates,
    *,
    detect,
):
    try:
        plate_detections = detect(
            image,
            "plate",
            top_k=3,
        )
        if not plate_detections:
            return None
        pot_detections = detect(
            image,
            "pot",
            top_k=3,
        )
    except Exception:  # noqa: BLE001
        return None

    def lowest_world_detection(detections):
        rows = []
        for detection in detections:
            try:
                uv = detection.centroid
                world = np.asarray(
                    state.env.pixel_to_world(
                        int(uv[0]),
                        int(uv[1]),
                    ),
                    dtype=float,
                )
            except Exception:  # noqa: BLE001
                continue
            if world.shape == (3,) and np.all(np.isfinite(world)):
                rows.append((float(world[2]), detection, world))
        return min(rows, key=lambda row: row[0]) if rows else None

    plate_row = lowest_world_detection(plate_detections)
    if plate_row is None:
        return None
    selected_detections = [plate_row[1]]
    pot_row = lowest_world_detection(pot_detections)
    if pot_row is not None:
        selected_detections.append(pot_row[1])
    excluded = [
        np.asarray(row.centroid, dtype=float)
        for row in selected_detections
    ]
    table_z = float(plate_row[2][2])

    rows = []
    for candidate in candidates:
        source = str(candidate.get("source") or "").lower()
        if (
            source.startswith("detector:")
            and "bowl" not in source
            and "dish" not in source
        ):
            continue
        uv = np.asarray(
            [int(candidate["u"]), int(candidate["v"])],
            dtype=float,
        )
        if any(
            float(np.linalg.norm(uv - center)) <= 24.0
            for center in excluded
        ):
            continue
        try:
            world = np.asarray(
                state.env.pixel_to_world(
                    int(uv[0]),
                    int(uv[1]),
                ),
                dtype=float,
            )
        except Exception:  # noqa: BLE001
            continue
        if world.shape != (3,) or not np.all(np.isfinite(world)):
            continue
        if not table_z - 0.02 <= float(world[2]) <= table_z + 0.08:
            continue
        rows.append((int(candidate["v"]), candidate))
    if not rows:
        return None
    selected = max(rows, key=lambda row: row[0])[1]
    return int(selected["u"]), int(selected["v"])


def _sam_refine_point(state, uv):
    """Refine a coarse VLM/Molmo point into a precise object-centroid pixel via a
    SAM point-prompt mask. The pointer picks the RIGHT object; SAM tightens WHERE
    on it (the point → mask → centroid step of the Molmo→SAM pipeline). Falls back
    to the raw point if SAM can't segment there."""
    if uv is None:
        return None
    rgb = state.env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return uv
    try:
        mask = sam_mask_at_point(rgb, int(uv[0]), int(uv[1]))
    except Exception:  # noqa: BLE001
        return uv
    if mask is None or int(mask.sum()) < 10:
        return uv
    ys, xs = np.where(mask)
    refined = int(np.median(xs)), int(np.median(ys))
    max_shift = float(
        os.environ.get("ROBORSI_SAM_REFINE_MAX_SHIFT_PX", "24")
    )
    shift = float(
        np.linalg.norm(
            np.asarray(refined, dtype=float)
            - np.asarray(uv, dtype=float)
        )
    )
    return uv if shift > max_shift else refined


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
    write_image_atomic(path, cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
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


def locate_by_owlv2(env, rgb, target: str, scale: int = 3, thresh: float = 0.05):
    """OWLv2-large queried only with the requested target phrase.

    Runs on the native head frame upscaled `scale` times and returns the
    highest-score box center in native pixels. No simulator object inventory is
    consulted.
    """
    import cv2
    import torch
    from PIL import Image
    query = str(target or "").strip()
    if not query:
        return None
    o = _load_owlv2()
    img = np.asarray(rgb).astype(np.uint8)
    up = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale),
                    interpolation=cv2.INTER_CUBIC)
    pil = Image.fromarray(up)
    sc = scale
    texts = [[f"a photo of {query}"]]
    inp = o["proc"](
        text=texts,
        images=pil,
        return_tensors="pt",
        truncation=True,
        max_length=16,
    ).to(o["dev"])
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


def _retreat_height(
    current_z: float,
    *,
    lift: float,
    clear_z: float,
    z_ceiling: float,
) -> float:
    current = float(current_z)
    desired = max(current + max(float(lift), 0.0), float(clear_z))
    if current >= float(z_ceiling):
        return current
    return max(current, min(desired, float(z_ceiling)))


def retreat_from_head_view(env, ctrl, lift: float = 0.18, back: float = 0.22,
                           clear_z: float = 0.42, z_ceiling: float = 1.15,
                           quat=None):
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
    z = _retreat_height(
        float(ee[2]),
        lift=lift,
        clear_z=clear_z,
        z_ceiling=z_ceiling,
    )
    reached, _ = ctrl.servo_to(
        [
            float(ee[0]) + float(step[0]),
            float(ee[1]) + float(step[1]),
            z,
        ],
        quat=quat,
        gripper="close",
        max_iters=70,
    )
    if reached:
        return True

    # A diagonal lift-and-back path can collide with the cabinet or held object.
    # Recover with two simpler motions: gain vertical clearance in place, then
    # translate a shorter distance toward the robot base.
    ee, _, _ = ctrl.read_pose()
    lift_z = _retreat_height(
        float(ee[2]),
        lift=min(lift, 0.10),
        clear_z=clear_z,
        z_ceiling=z_ceiling,
    )
    lifted, _ = ctrl.servo_to(
        [float(ee[0]), float(ee[1]), lift_z],
        quat=quat,
        gripper="close",
        max_iters=70,
        via_trajopt=True,
    )
    if not lifted:
        return False
    ee, _, _ = ctrl.read_pose()
    horiz = base[:2] - ee[:2]
    n = float(np.linalg.norm(horiz))
    if n <= 1e-6:
        return True
    side_step = (horiz / n) * min(back, 0.12)
    shifted, _ = ctrl.servo_to(
        [
            float(ee[0]) + float(side_step[0]),
            float(ee[1]) + float(side_step[1]),
            float(ee[2]),
        ],
        quat=quat,
        gripper="close",
        max_iters=70,
        via_trajopt=True,
    )
    return bool(shifted)


def localize_precise(state, obj: str, route: str = "vlm_sam"):
    """Localize `obj` on the head frame → (u,v) or None, by one of TWO routes the
    Engineer picks (via the find_by_detector / find_by_pointing skills):
      * 'owlv2'   — open-vocab DETECTOR: OWLv2 text→box (→ SAM3-text → tiny
                    detector). Fast; but text-match CANNOT reason about "the bowl
                    BETWEEN the plate and ramekin" — grabs the wrong look-alike.
      * 'vlm_sam' — POINT→MASK: a VLM pointer (local Qwen2.5-VL, else Claude
                    vlm_point) reads the full referring expression and points at
                    the RIGHT object, then a SAM point-prompt mask centroid tightens
                    the pixel. Disambiguates look-alikes a detector cannot."""
    env = state.env
    rgb = env.take_snapshot().images.get("head_camera")
    if rgb is None:
        return None
    if route == "owlv2":
        uv = locate_by_owlv2(env, rgb, obj)          # detector: text → box
        if uv is not None:
            return _sam_refine_point(state, uv)
        uv = locate_by_sam3(rgb, obj)
        if uv is not None:
            return uv
        uv = locate_pixel(rgb, obj)
        return zoom_localize(state, obj, uv) if uv is not None else None
    from roborsi.embodied.sim.libero.run_records import (
        classify_infrastructure_exception,
    )

    transport_exc = None

    def remember(exc):
        nonlocal transport_exc
        if (
            transport_exc is None
            and classify_infrastructure_exception(exc)
            == "transport_failure"
        ):
            transport_exc = exc

    point_query = _semantic_point_query(obj)

    # vlm_sam: first accept agreement between two independent semantic pointers.
    # Otherwise combine both with detector candidates and ask a separate
    # image-only verifier to select the exact identity / relation.
    try:
        local_uv = local_vlm_point(state, point_query)
    except Exception as exc:  # noqa: BLE001
        remember(exc)
        local_uv = None
    try:
        remote_uv = vlm_point(state, point_query)
    except Exception as exc:  # noqa: BLE001
        remember(exc)
        remote_uv = None
    if (
        remote_uv is not None
        and os.environ.get(
            "ROBORSI_GPT_POINTER_AUTHORITATIVE",
            "0",
        )
        == "1"
    ):
        return _sam_refine_point(state, remote_uv)
    uv = None
    consensus_uv = None
    if local_uv is not None and remote_uv is not None:
        distance = float(
            np.linalg.norm(
                np.asarray(local_uv, dtype=float)
                - np.asarray(remote_uv, dtype=float)
            )
        )
        if distance <= 24.0:
            consensus_uv = (
                int(round((local_uv[0] + remote_uv[0]) / 2.0)),
                int(round((local_uv[1] + remote_uv[1]) / 2.0)),
            )
    normalized_obj = " ".join(str(obj or "").lower().split())
    depth_relation_query = bool(
        "bowl" in normalized_obj
        and (
            " on top of " in f" {normalized_obj} "
            or " on the top of " in f" {normalized_obj} "
            or " in the top layer " in f" {normalized_obj} "
            or " in top layer " in f" {normalized_obj} "
            or " in the top drawer " in f" {normalized_obj} "
            or " inside the top drawer " in f" {normalized_obj} "
        )
    )
    if consensus_uv is not None and not depth_relation_query:
        return _sam_refine_point(state, consensus_uv)

    candidates = _ranked_localization_candidates(
        rgb,
        obj,
        [value for value in (local_uv, remote_uv) if value is not None],
    )
    relation_selected = _choose_depth_relation_candidate(
        state,
        obj,
        candidates,
    )
    if relation_selected is not None:
        return _sam_refine_point(state, relation_selected)
    selected = None
    if depth_relation_query and candidates:
        try:
            selected = _choose_localization_candidate(
                state,
                point_query,
                rgb,
                candidates,
            )
        except Exception as exc:  # noqa: BLE001
            remember(exc)
            selected = None
        if selected is not None:
            return _sam_refine_point(state, selected)
    if consensus_uv is not None:
        return _sam_refine_point(state, consensus_uv)
    if candidates:
        try:
            selected = _choose_localization_candidate(
                state,
                point_query,
                rgb,
                candidates,
            )
        except Exception as exc:  # noqa: BLE001
            remember(exc)
            selected = None
        if selected is not None:
            uv = selected
    if uv is None:
        uv = local_uv or remote_uv
    if uv is not None:
        return _sam_refine_point(state, uv)           # SAM point-prompt → precise centroid
    if transport_exc is not None:
        raise transport_exc
    try:
        uv = locate_by_sam3(rgb, obj)
    except Exception as exc:  # noqa: BLE001
        remember(exc)
        uv = None
    if uv is not None:
        return uv
    try:
        uv = locate_by_owlv2(env, rgb, obj)
    except Exception as exc:  # noqa: BLE001
        remember(exc)
        uv = None
    if uv is not None:
        return uv
    if transport_exc is not None:
        raise transport_exc
    return None


def _load_point_sam():
    if "model" in _POINT_SAM:
        return _POINT_SAM["processor"], _POINT_SAM["model"]
    import torch
    from transformers import SamModel, SamProcessor

    model_name = os.environ.get(
        "ROBORSI_POINT_SAM_MODEL",
        "facebook/sam-vit-base",
    )
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
        point_limit = max(
            1,
            int(round(3500 * (float(h * w) / float(256 * 256)))),
        )
        if len(pts) > point_limit:             # still whole-table after cleaning → reject
            return None
    return pts if len(pts) >= 30 else None


# NOTE — wrist multiview fusion was investigated (CaP-X fuses agentview + wrist)
# and DELIBERATELY NOT ADOPTED on LIBERO. CaP works because on its Franka setup
# the wrist looks along the approach at the commanded object; on LIBERO the wrist
# (``robot0_eye_in_hand``) is rigidly mounted on the HOME-pose gripper and, at
# grasp/localize time (arm at reset, before any approach), stares straight DOWN
# (optical axis ≈ [0,0,-1]) at a FIXED table spot near the arm base (~[-0.16,-0.02])
# that does not coincide with the commanded target. Measured on this box:
#   • libero_spatial/0 (two black bowls): head localizes bowl_2 @ y=0.32, but the
#     wrist is aimed at bowl_1 @ y=0.20 — 0.34 m away. Its own SAM mask segments
#     the WRONG bowl (cloud centroid 0.113–0.194 m off the head target), so CaP's
#     own <1 cm proximity-union gate REJECTS the fusion and falls back to head.
#   • libero_object/0 (grocery): the target is far in +y, out of the wrist frame —
#     SAM3 returns NOTHING in the wrist view. Pure no-op.
#   • A naive geometric bbox-crop (the earlier prototype) adds only +10/+46 stray
#     points to a 127–722-pt head cloud, from a DIFFERENT bowl / neighbour clutter,
#     nudging the GraspGen point <1 cm and risking a pull toward the wrong object.
# So on LIBERO the wrist adds no correct-object geometry at grasp time; fusing it
# is a no-op at best and mildly harmful at worst. The grasp cloud stays HEAD-ONLY.
# Probes: /tmp/mv_probe{,2,3,4}.py (frames in /tmp/mv_probe_out/).


def grasps_at_pixel(env, u: int, v: int, top_k: int = 3):
    """(top-K GraspGen 6-DoF grasps, object cloud) for the object under (u,v)."""
    cloud = object_cloud(env, u, v)
    if cloud is None:
        return [], None
    grasps = []
    if os.environ.get("GRASPGEN_PORT"):
        try:
            from roborsi.embodied.sim.robotwin.graspgen_infer import (
                _grasps_from_cloud,
            )

            grasps = _grasps_from_cloud(
                cloud.astype(np.float32),
                top_k=top_k,
            )
        except Exception:
            grasps = []
    filtered = filter_grasps_consistent_with_cloud(grasps, cloud)
    if filtered:
        return filtered, cloud
    point = np.median(np.asarray(cloud, dtype=float), axis=0)
    return [{
        "score": 0.0,
        "translation_tcp_world": point.tolist(),
        "rotation_matrix_world": None,
        "source": "sam+depth-topdown",
    }], cloud


def filter_grasps_consistent_with_cloud(
    grasps,
    cloud,
    *,
    max_distance: float | None = None,
):
    points = np.asarray(cloud, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3:
        return []
    points = points[np.all(np.isfinite(points), axis=1)]
    if len(points) == 0:
        return []
    if max_distance is None:
        max_distance = float(
            os.environ.get(
                "ROBORSI_GRASP_CLOUD_MAX_DISTANCE",
                "0.06",
            )
        )
    limit = float(max_distance)
    if not np.isfinite(limit) or limit <= 0.0:
        raise ValueError("max grasp-cloud distance must be positive")
    accepted = []
    for grasp in grasps:
        try:
            target = np.asarray(
                grasp["translation_tcp_world"],
                dtype=float,
            ).reshape(-1)
        except (KeyError, TypeError, ValueError):
            continue
        if target.shape != (3,) or not np.all(np.isfinite(target)):
            continue
        nearest = float(
            np.linalg.norm(points - target[None, :], axis=1).min()
        )
        if nearest <= limit:
            accepted.append(grasp)
    return accepted


def _fit_circle_xy(points: np.ndarray):
    xy = np.asarray(points, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) < 8:
        return None
    finite = xy[np.all(np.isfinite(xy), axis=1)]
    if len(finite) < 8:
        return None
    x, y = finite[:, 0], finite[:, 1]
    design = np.column_stack([x, y, np.ones(len(finite))])
    target = x ** 2 + y ** 2
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    cx = 0.5 * float(solution[0])
    cy = 0.5 * float(solution[1])
    if not (np.isfinite(cx) and np.isfinite(cy)):
        return None
    distances = np.hypot(x - cx, y - cy)
    radius = float(np.median(distances))
    if not np.isfinite(radius) or radius <= 0.0:
        return None
    circularity = float(
        np.mean(np.abs(distances - radius) <= 0.12 * radius)
    )
    return np.array([cx, cy], dtype=float), radius, circularity


def _wide_hollow_rim_plan(
    points,
    *,
    robot_base_xy,
    max_opening: float = 0.08,
):
    """Return a pure-RGB-D rim pinch plan for a wide hollow round object."""
    cloud = np.asarray(points, dtype=float)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return None
    cloud = cloud[np.all(np.isfinite(cloud), axis=1)]
    if len(cloud) < 30:
        return None

    z_hi = float(cloud[:, 2].max())
    z_lo = float(cloud[:, 2].min())
    rim = None
    rim_fit = None
    top = z_hi
    while top - 0.012 >= z_lo:
        band = cloud[
            (cloud[:, 2] <= top)
            & (cloud[:, 2] >= top - 0.012)
        ]
        fit = _fit_circle_xy(band[:, :2])
        if fit is not None:
            center, radius, circularity = fit
            if (
                0.5 * float(max_opening) < radius <= 0.12
                and circularity >= 0.85
            ):
                rim = band
                rim_fit = fit
                break
        top -= 0.006
    if rim is None or rim_fit is None:
        return None
    center, rim_radius, _ = rim_fit

    center_hint = np.median(cloud[:, :2], axis=0)
    if float(np.linalg.norm(center - center_hint)) > 0.07:
        return None
    radial_distance = np.linalg.norm(cloud[:, :2] - center, axis=1)
    interior = cloud[radial_distance <= 0.55 * rim_radius]
    if len(interior) < 8:
        return None
    interior_z = float(np.median(interior[:, 2]))
    rim_z = float(np.median(rim[:, 2]))
    if rim_z - interior_z < 0.025:
        return None

    base = np.asarray(robot_base_xy, dtype=float)
    if base.shape != (2,) or not np.all(np.isfinite(base)):
        return None
    jaw = base - center
    jaw_norm = float(np.linalg.norm(jaw))
    if jaw_norm <= 1e-8:
        return None
    preferred_jaw = jaw / jaw_norm
    close_z = rim_z - 0.012
    wall = cloud[np.abs(cloud[:, 2] - close_z) <= 0.006]
    if len(wall) < 8:
        return None
    wall_vectors = wall[:, :2] - center
    wall_radius = np.linalg.norm(wall_vectors, axis=1)
    supported = (
        (wall_radius >= 0.45 * rim_radius)
        & (wall_radius <= 1.05 * rim_radius)
    )
    if int(supported.sum()) < 8:
        return None
    wall = wall[supported]
    wall_vectors = wall_vectors[supported]
    wall_radius = wall_radius[supported]
    wall_directions = wall_vectors / wall_radius[:, None]
    wall_angles = np.arctan2(
        wall_directions[:, 1],
        wall_directions[:, 0],
    )
    angular_delta = np.abs(
        np.arctan2(
            np.sin(wall_angles[:, None] - wall_angles[None, :]),
            np.cos(wall_angles[:, None] - wall_angles[None, :]),
        )
    )
    local_support = np.sum(angular_delta <= 0.14, axis=1)
    supported_indices = np.flatnonzero(local_support >= 6)
    if len(supported_indices) == 0:
        return None
    angular_score = wall_directions @ preferred_jaw
    height_penalty = (
        np.abs(wall[:, 2] - close_z) / 0.006
    )
    scores = angular_score - 0.25 * height_penalty
    best = int(
        supported_indices[
            np.argmax(scores[supported_indices])
        ]
    )
    neighborhood = angular_delta[best] <= 0.14
    jaw = np.median(wall_directions[neighborhood], axis=0)
    jaw_norm = float(np.linalg.norm(jaw))
    if jaw_norm <= 1e-8:
        return None
    jaw = jaw / jaw_norm
    close_radius = float(np.median(wall_radius[neighborhood]))
    grasp_xy = center + close_radius * jaw
    return {
        "center_xy": center,
        "grasp_xy": grasp_xy,
        "jaw_xy": jaw,
        "exit_xy": preferred_jaw,
        "radius": close_radius,
        "rim_radius": rim_radius,
        "rim_z": rim_z,
        "close_z": close_z,
    }


def execute_rim_grip(
    env,
    plan,
    *,
    hover: float = 0.10,
    lift: float = 0.07,
    exit_distance: float = 0.0,
):
    """Pinch one wall of a measured wide rim; never close unless descent lands."""
    from scipy.spatial.transform import Rotation

    from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
    from roborsi.embodied.skills.base._lib.libero.gripper_state import (
        GripperState,
    )

    ctrl = LiberoControl(env)
    grasp_xy = np.asarray(plan["grasp_xy"], dtype=float)
    jaw_xy = np.asarray(plan["jaw_xy"], dtype=float)
    close_z = float(plan["close_z"])
    p = np.array([grasp_xy[0], grasp_xy[1], close_z], dtype=float)

    _, current_quat, _ = ctrl.read_pose()
    current_quat = np.asarray(current_quat, dtype=float)
    desired_jaw_norm = float(np.linalg.norm(jaw_xy))
    if desired_jaw_norm <= 1e-8:
        ee, _, gq = ctrl.read_pose()
        return p, np.asarray(ee), gq
    jaw = np.array(
        [jaw_xy[0], jaw_xy[1], 0.0],
        dtype=float,
    )
    jaw = jaw / np.linalg.norm(jaw)
    approach = np.array([0.0, 0.0, -1.0], dtype=float)

    def topdown_quat(jaw_axis):
        x_axis = np.cross(jaw_axis, approach)
        matrix = np.column_stack([x_axis, jaw_axis, approach])
        return Rotation.from_matrix(matrix).as_quat()

    candidates = [topdown_quat(jaw), topdown_quat(-jaw)]
    if current_quat.shape == (4,) and np.all(np.isfinite(current_quat)):
        norm = float(np.linalg.norm(current_quat))
        if norm > 0.0:
            normalized_current = current_quat / norm
            target_quat = max(
                candidates,
                key=lambda candidate: abs(
                    float(np.dot(candidate, normalized_current))
                ),
            )
        else:
            target_quat = candidates[0]
    else:
        target_quat = candidates[0]

    ctrl.set_gripper(close=False)
    hover_pose = p + np.array([0.0, 0.0, float(hover)], dtype=float)
    hover_reached, _ = ctrl.servo_to(
        hover_pose,
        quat=target_quat,
        gripper="open",
        pos_tol=0.015,
        rot_tol=0.08,
        max_iters=100,
    )
    if not hover_reached:
        ee, _, gq = ctrl.read_pose()
        return p, np.asarray(ee), gq
    descent_reached, _ = ctrl.servo_to(
        p,
        quat=target_quat,
        gripper="open",
        pos_tol=0.012,
        rot_tol=0.08,
        max_iters=100,
    )
    if not descent_reached:
        ee, _, gq = ctrl.read_pose()
        return p, np.asarray(ee), gq

    ctrl.set_gripper(close=True)
    _, grip_state = ctrl.read_gripper_state()
    if grip_state is GripperState.HELD:
        lift_origin = p
        distance = float(exit_distance)
        if distance > 0.0:
            exit_xy = np.asarray(plan.get("exit_xy"), dtype=float)
            norm = (
                float(np.linalg.norm(exit_xy))
                if exit_xy.shape == (2,)
                and np.all(np.isfinite(exit_xy))
                else 0.0
            )
            if norm <= 1e-8 or not np.isfinite(distance):
                ctrl.set_gripper(close=False)
                ee, _, gq = ctrl.read_pose()
                return p, np.asarray(ee), gq
            exit_xy = exit_xy / norm
            exit_pose = p + np.array(
                [
                    float(exit_xy[0]) * distance,
                    float(exit_xy[1]) * distance,
                    0.0,
                ],
                dtype=float,
            )
            exited, _ = ctrl.servo_to(
                exit_pose,
                quat=target_quat,
                gripper="close",
                pos_tol=0.015,
                rot_tol=0.08,
                max_iters=100,
            )
            if not exited:
                ctrl.set_gripper(close=False)
                ee, _, gq = ctrl.read_pose()
                return p, np.asarray(ee), gq
            lift_origin = exit_pose
        ctrl.servo_to(
            lift_origin + np.array([0.0, 0.0, float(lift)], dtype=float),
            quat=target_quat,
            gripper="close",
            pos_tol=0.015,
            rot_tol=0.08,
            max_iters=80,
        )
    ee, _, gq = ctrl.read_pose()
    return p, np.asarray(ee), gq


def opposite_rim_plan(plan):
    mirrored = dict(plan)
    center = np.asarray(plan["center_xy"], dtype=float)
    grasp = np.asarray(plan["grasp_xy"], dtype=float)
    jaw = np.asarray(plan["jaw_xy"], dtype=float)
    mirrored["grasp_xy"] = center - (grasp - center)
    mirrored["jaw_xy"] = -jaw
    return mirrored


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


def execute_package_side_entry(
    env,
    grasp: dict,
    cloud,
    *,
    hover: float = 0.10,
    side_distance: float = 0.07,
    contact_clearance: float = 0.025,
    lift: float = 0.05,
):
    """Descend beside a low package, then enter horizontally before closing."""
    from roborsi.embodied.skills.base._lib.libero._control import (
        LiberoControl,
    )
    from roborsi.embodied.skills.base._lib.libero.gripper_state import (
        GripperState,
    )

    points = np.asarray(cloud, dtype=float)
    points = (
        points[np.all(np.isfinite(points), axis=1)]
        if points.ndim == 2 and points.shape[1] == 3
        else np.empty((0, 3), dtype=float)
    )
    target = np.asarray(
        grasp.get("translation_tcp_world"),
        dtype=float,
    ).reshape(-1)
    evidence = {
        "side_entry_succeeded": False,
        "side_candidates_attempted": 0,
        "side_candidate_index": None,
    }
    ctrl = LiberoControl(env)
    if (
        len(points) < 10
        or target.shape != (3,)
        or not np.all(np.isfinite(target))
    ):
        ee, _, gq = ctrl.read_pose()
        return target, np.asarray(ee), gq, evidence

    base = np.asarray(env.robot_base_pos(), dtype=float).reshape(-1)
    if base.size < 2 or not np.all(np.isfinite(base[:2])):
        ee, _, gq = ctrl.read_pose()
        return target, np.asarray(ee), gq, evidence
    toward_base = base[:2] - target[:2]
    norm = float(np.linalg.norm(toward_base))
    if norm <= 1e-8:
        ee, _, gq = ctrl.read_pose()
        return target, np.asarray(ee), gq, evidence
    toward_base /= norm
    perpendicular = np.array(
        [-toward_base[1], toward_base[0]],
        dtype=float,
    )
    directions = (
        toward_base,
        -toward_base,
        perpendicular,
        -perpendicular,
    )
    distance = float(np.clip(side_distance, 0.04, 0.09))
    hover = float(np.clip(hover, 0.06, 0.16))
    clearance = float(np.clip(contact_clearance, 0.015, 0.04))
    lift = float(np.clip(lift, 0.03, 0.08))
    contact_z = float(np.percentile(points[:, 2], 5)) + clearance
    target_contact = np.array(
        [target[0], target[1], contact_z],
        dtype=float,
    )

    ctrl.set_gripper(close=False)
    for index, direction in enumerate(directions):
        evidence["side_candidates_attempted"] += 1
        side = target_contact.copy()
        side[:2] += distance * direction
        side_hover = side + np.array([0.0, 0.0, hover], dtype=float)
        reached, _ = ctrl.servo_to(
            side_hover,
            gripper="open",
            pos_tol=0.015,
            max_iters=80,
        )
        if not reached:
            continue
        reached, _ = ctrl.servo_to(
            side,
            gripper="open",
            pos_tol=0.012,
            max_iters=80,
        )
        if not reached:
            ctrl.servo_to(side_hover, gripper="open", max_iters=40)
            continue
        entered, _ = ctrl.servo_to(
            target_contact,
            gripper="open",
            pos_tol=0.012,
            max_iters=80,
        )
        if not entered:
            ctrl.servo_to(side_hover, gripper="open", max_iters=40)
            continue

        ctrl.set_gripper(close=True)
        _, grip_state = ctrl.read_gripper_state()
        if grip_state is not GripperState.HELD:
            ctrl.set_gripper(close=False)
            ctrl.servo_to(side_hover, gripper="open", max_iters=40)
            continue
        ctrl.servo_to(
            target_contact + np.array([0.0, 0.0, lift], dtype=float),
            gripper="close",
            pos_tol=0.015,
            max_iters=60,
        )
        _, grip_state = ctrl.read_gripper_state()
        if grip_state is GripperState.HELD:
            evidence["side_entry_succeeded"] = True
            evidence["side_candidate_index"] = index
            ee, _, gq = ctrl.read_pose()
            return target_contact, np.asarray(ee), gq, evidence
        ctrl.set_gripper(close=False)
        ctrl.servo_to(side_hover, gripper="open", max_iters=40)

    ee, _, gq = ctrl.read_pose()
    return target_contact, np.asarray(ee), gq, evidence


def _servo_grasp_target(
    ctrl,
    target,
    *,
    quat=None,
    gripper: str,
    pos_tol: float = 0.015,
    via_trajopt: bool = False,
):
    target = np.asarray(target, dtype=float)
    reached, last = ctrl.servo_to(
        target,
        quat=quat,
        gripper=gripper,
        pos_tol=pos_tol,
        via_trajopt=via_trajopt,
    )
    if reached:
        return True, last

    measured, _, _ = ctrl.read_pose()
    from roborsi.embodied.skills.base._lib.libero._control import (
        bounded_residual_correction_target,
    )

    correction_target = bounded_residual_correction_target(
        target,
        measured,
        max_total_error=0.12,
        max_xy_error=0.08,
        max_z_error=0.08,
        max_move=0.08,
    )
    if correction_target is None:
        return False, last
    correction_reached, correction_last = ctrl.servo_correction_to(
        correction_target,
        quat=quat,
        gripper=gripper,
        pos_tol=pos_tol,
        max_iters=60,
    )
    measured, _, _ = ctrl.read_pose()
    position_error = float(
        np.linalg.norm(np.asarray(measured, dtype=float) - target)
    )
    return bool(correction_reached and position_error <= pos_tol), (
        correction_last if correction_last is not None else last
    )


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
            adjusted = dict(grasp)
            tcp = np.asarray(
                grasp["translation_tcp_world"],
                dtype=float,
            ).copy()
            tcp[2] += float(z_offset)
            adjusted["translation_tcp_world"] = tcp
            return execute_6dof(env, adjusted, cloud=cloud, lift=hover)
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
        p[2] = max(p[2], float(cloud[:, 2].min()) + 0.001)
    q = None
    if yaw:
        import robosuite.utils.transform_utils as T
        _, cur_q, _ = ctrl.read_pose()
        qz = T.axisangle2quat(np.array([0.0, 0.0, float(yaw)]))
        q = T.quat_multiply(qz, cur_q)          # yaw the gripper about world +Z
    ctrl.set_gripper(close=False)
    reached, _ = _servo_grasp_target(
        ctrl,
        [p[0], p[1], p[2] + hover],
        quat=q,
        gripper="open",
        via_trajopt=True,
    )
    if not reached:
        ee, _, gq = ctrl.read_pose()
        return p, np.asarray(ee), gq
    reached, _ = _servo_grasp_target(
        ctrl,
        [p[0], p[1], p[2]],
        quat=q,
        gripper="open",
        via_trajopt=False,
    )
    if not reached:
        ee, _, gq = ctrl.read_pose()
        return p, np.asarray(ee), gq
    ctrl.set_gripper(close=True)
    ctrl.servo_to([p[0], p[1], p[2] + hover], quat=q, gripper="close")
    ee, _, gq = ctrl.read_pose()
    return p, np.asarray(ee), gq


def execute_base_grip(
    env,
    cloud,
    hover: float = 0.10,
    z_offset: float = 0.0,
    max_retries: int = 5,
):
    """Grip a HOLLOW/bowl object by its SOLID BASE. GraspGen's top-down point lands on
    the mid-shell, where parallel jaws close on the cavity (gap→0.001, on air); the base
    is solid (a ~5mm bottom) and narrow enough to pinch. Aim at the BASE-DISC centre —
    the centroid of the LOWEST cloud points, NOT the whole shell: the shell centroid
    sits ~1.5cm off the small solid foot on some poses and closes on air (verified:
    same-bowl seeds flipped MISS→LIFT when switching shell-centroid → base-disc centre).
    If the first close still meets air, a few small nudges (deeper, ±xy) recover it —
    ALL inside this ONE skill call, so the agent never sees a retry. Verified 8/8 lift.
    Returns (grasp_point, final_ee_pos, gripper_qpos) like execute_topdown."""
    from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
    from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
    ctrl = LiberoControl(env)
    pts = np.asarray(cloud)
    zbase = float(np.percentile(pts[:, 2], 5))                # low percentile, robust to stray points
    low = pts[pts[:, 2] < zbase + 0.012]                      # the base disc / foot ring
    c = low[:, :2].mean(axis=0) if len(low) >= 5 else pts[:, :2].mean(axis=0)

    def _grip(x, y, z):
        z = max(float(z), zbase + 0.001)
        ctrl.set_gripper(close=False)
        reached, _ = ctrl.servo_to(
            [x, y, z + hover],
            gripper="open",
        )
        if not reached:
            ee, _, gq = ctrl.read_pose()
            return ee, gq, GripperState.OPEN
        reached, _ = ctrl.servo_to(
            [x, y, z],
            gripper="open",
        )
        if not reached:
            ee, _, gq = ctrl.read_pose()
            return ee, gq, GripperState.OPEN
        ctrl.set_gripper(close=True)
        ctrl.servo_to([x, y, z + hover], gripper="close")
        ee, _, gq = ctrl.read_pose()
        _, grip_state = ctrl.read_gripper_state()
        return ee, gq, grip_state

    z0 = max(zbase + 0.001, zbase + 0.004 + float(z_offset))
    ee, gq, grip_state = _grip(float(c[0]), float(c[1]), z0)
    p = np.array([c[0], c[1], z0])
    if grip_state is not GripperState.HELD:
        offsets = [
            (0.0, 0.0, -0.004),
            (0.012, 0.0, 0.004),
            (-0.012, 0.0, 0.004),
            (0.0, 0.012, 0.004),
            (0.0, -0.012, 0.004),
        ]
        retry_count = int(np.clip(max_retries, 0, len(offsets)))
        for dx, dy, dz in offsets[:retry_count]:
            retry_z = max(z0 + dz, zbase + 0.001)
            ee, gq, grip_state = _grip(
                float(c[0] + dx),
                float(c[1] + dy),
                retry_z,
            )
            if grip_state is GripperState.HELD:
                p = np.array([c[0] + dx, c[1] + dy, retry_z])
                break
    return p, np.asarray(ee), gq
