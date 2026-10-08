"""grasp_object — composite pick (base/libero).

Pure perception path only: locate the object from a pixel (or name), segment via
SAM, unproject with depth, run GraspGen, and execute a top-down pick.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base._lib.libero.semantic_point import (
    matching_semantic_point,
)
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    capture_depth_frame,
    clear_visual_hold,
    record_pending_visual_hold,
    record_visual_hold,
    verify_visual_hold,
)

# LIBERO head frames render at 256px; the perception fallbacks emit the image
# CENTRE (128,128) as a "found nothing" sentinel. Grasping there always closes
# on empty table AND makes the VLM re-find_pixel forever (budget_exceeded), so we
# treat it as a hard localization failure instead of a real detection.
_SENTINEL_UV = (128, 128)
_SENTINEL_TOL = 2  # pixels
_SOURCE_PATCH_RADIUS = 12
_SOURCE_STATIC_MAD_MAX = 3.0
_CABINET_EXIT_DISTANCE = 0.16


def _fix_on() -> bool:
    """Pure-vision grasp fixes (SAM3-first localize, self-correcting re-localize,
    rim yaw-sweep) are ON by default — verified 46cm→4cm grasp error, 25%→82%
    right-object. ``ROBORSI_GRASP_FIX=0`` restores the old unguarded path."""
    return os.environ.get("ROBORSI_GRASP_FIX", "1") != "0"


def _is_sentinel(uv) -> bool:
    if uv is None:
        return False
    try:
        u, v = int(uv[0]), int(uv[1])
    except (TypeError, ValueError, OverflowError, IndexError):
        return False
    su, sv = _SENTINEL_UV
    return abs(u - su) <= _SENTINEL_TOL and abs(v - sv) <= _SENTINEL_TOL


def _source_patch_motion(before, after, uv) -> float:
    if before is None or after is None:
        return float("inf")
    a = np.asarray(before)
    b = np.asarray(after)
    if a.shape != b.shape or a.ndim != 3:
        return float("inf")
    u, v = int(uv[0]), int(uv[1])
    h, w = a.shape[:2]
    x0, x1 = max(0, u - _SOURCE_PATCH_RADIUS), min(w, u + _SOURCE_PATCH_RADIUS)
    y0, y1 = max(0, v - _SOURCE_PATCH_RADIUS), min(h, v + _SOURCE_PATCH_RADIUS)
    if x0 >= x1 or y0 >= y1:
        return float("inf")
    pa = a[y0:y1, x0:x1].astype(np.float32)
    pb = b[y0:y1, x0:x1].astype(np.float32)
    return float(np.abs(pa - pb).mean())


def _bounded_arg(args, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(args.get(name, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    if not np.isfinite(value):
        value = default
    return max(low, min(high, value))


def _normalized_object_name(value: Any) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower().replace("_", " "))
    return " ".join(
        token
        for token in tokens
        if token not in {"a", "an", "the"}
    )


def _same_object_name(left: Any, right: Any) -> bool:
    a = _normalized_object_name(left)
    b = _normalized_object_name(right)
    return bool(a and b and a == b)


def _uses_hollow_base_grip(value: Any) -> bool:
    words = set(_normalized_object_name(value).split())
    return bool(words & {"bowl", "cup", "mug", "ramekin"})


def _uses_floor_level_package_grip(value: Any) -> bool:
    words = set(_normalized_object_name(value).split())
    return bool(words & {"box", "carton", "cheese", "package"})


def _uses_side_entry_package_grip(
    value: Any,
    package_hint: Any = None,
) -> bool:
    words = set(_normalized_object_name(value).split())
    hint = _normalized_object_name(package_hint)
    return bool(words & {"bottle", "can", "jar"}) or hint in {
        "bottle",
        "can",
        "jar",
    }


def _requires_horizontal_cabinet_exit(value: Any) -> bool:
    text = _normalized_object_name(value)
    words = set(text.split())
    return bool(
        "cabinet" in words
        and ("in" in words or "inside" in words)
        and bool(words & {"layer", "shelf", "drawer"})
        and "on top of" not in text
    )


def _semantic_relocalization_consistent(
    source_uv: tuple[int, int],
    candidate_uv: tuple[int, int],
) -> bool:
    return bool(
        float(
            np.linalg.norm(
                np.asarray(source_uv, dtype=float)
                - np.asarray(candidate_uv, dtype=float)
            )
        )
        <= float(_SOURCE_PATCH_RADIUS * 2)
    )


def _verify_object_pixel(
    state: Any,
    object_name: str,
    rgb: Any,
    uv: tuple[int, int],
) -> bool:
    import cv2

    from roborsi.embodied.agent_loop.vlm_io import (
        _call_vlm_image,
        _parse_json,
    )
    from roborsi.embodied.skills.base._lib.libero._perception import (
        write_image_atomic,
    )

    image = np.asarray(rgb)
    if image.ndim != 3 or not str(object_name or "").strip():
        return False
    height, width = image.shape[:2]
    u, v = int(uv[0]), int(uv[1])
    if not (0 <= u < width and 0 <= v < height):
        return False
    scale = max(
        2,
        int(os.environ.get("ROBORSI_CANDIDATE_UPSCALE", "3")),
    )
    full = cv2.resize(
        image,
        (width * scale, height * scale),
        interpolation=cv2.INTER_CUBIC,
    )
    center = (u * scale, v * scale)
    marker_radius = max(6, int(round(min(full.shape[:2]) * 0.012)))
    cv2.circle(
        full,
        center,
        marker_radius,
        (255, 40, 40),
        max(2, scale),
    )

    crop_radius = max(_SOURCE_PATCH_RADIUS * 2, min(height, width) // 10)
    x0, x1 = max(0, u - crop_radius), min(width, u + crop_radius)
    y0, y1 = max(0, v - crop_radius), min(height, v + crop_radius)
    crop = np.asarray(image[y0:y1, x0:x1])
    if crop.size == 0:
        return False
    side = max(crop.shape[:2])
    top = (side - crop.shape[0]) // 2
    bottom = side - crop.shape[0] - top
    left = (side - crop.shape[1]) // 2
    right = side - crop.shape[1] - left
    crop = cv2.copyMakeBorder(
        crop,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_REPLICATE,
    )
    detail = cv2.resize(
        crop,
        (full.shape[0], full.shape[0]),
        interpolation=cv2.INTER_CUBIC,
    )
    crop_center = (
        int(round((u - x0 + left) * detail.shape[1] / crop.shape[1])),
        int(round((v - y0 + top) * detail.shape[0] / crop.shape[0])),
    )
    cv2.circle(
        detail,
        crop_center,
        marker_radius * 2,
        (255, 40, 40),
        max(3, scale),
    )
    annotated = np.concatenate([full, detail], axis=1)
    workdir = Path(
        getattr(state, "workdir", "/tmp/roborsi-grasp-identity")
    )
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "grasp_identity_candidate.png"
    write_image_atomic(
        path,
        cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR),
    )
    system = (
        "The left panel is the full current scene and the right panel is a "
        "magnified crop. The red ring marks one candidate grasp pixel. "
        "Judge only visible identity and spatial relationships. Similar cans, "
        "boxes, bowls, or packages are not interchangeable. Return one JSON "
        "object only: "
        '{"target_visible": <true|false>, '
        '"marker_on_target": <true|false>, '
        '"exact_identity_match": <true|false>, '
        '"confusable_alternative_at_marker": <true|false>, '
        '"reason": "<short>"}.'
    )
    user = (
        f"Requested target: {object_name}\n"
        "Does the marked pixel lie on that exact requested target?"
    )
    model = os.environ.get(
        "ROBORSI_PERCEPTION_MODEL",
        "anthropic/claude-sonnet-4-6",
    )
    parsed = _parse_json(_call_vlm_image(model, system, user, path))
    return bool(
        parsed
        and parsed.get("target_visible") is True
        and parsed.get("marker_on_target") is True
        and parsed.get("exact_identity_match") is True
        and parsed.get("confusable_alternative_at_marker") is False
    )


def _clear_source_view(
    env,
    ctrl,
) -> tuple[bool, bool, np.ndarray | None]:
    """Request one reduced collision-aware retreat and report hold separately.

    ``motion_reached`` is only the retreat helper's measured completion result.
    ``holding_after`` is independently derived from calibrated proprioception.
    The helper remains responsible for collision-aware execution; this function
    makes no claim about its internal waypoint slew.
    """
    from roborsi.embodied.skills.base._lib.libero._perception import (
        retreat_from_head_view,
    )

    motion_reached = bool(
        retreat_from_head_view(
            env,
            ctrl,
            lift=0.025,
            back=0.035,
            clear_z=0.36,
            z_ceiling=1.15,
        )
    )
    _, grip_state = ctrl.read_gripper_state()
    holding_after = grip_state is GripperState.HELD
    ee, _, _ = ctrl.read_pose()
    point = np.asarray(ee, dtype=float)
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        point = None
    return motion_reached, holding_after, point


# ── perception mode ──────────────────────────────────────────────────────
def _locate_pixel(state, args):
    name = str(args.get("object") or "").strip()
    from roborsi.embodied.skills.base._lib.libero._perception import (
        recall_pixel,
        remember_pixel,
    )

    # Prefer the pixel the VLM already found: find_pixel now runs vlm_point FIRST,
    # which disambiguates referring expressions ("the bowl BETWEEN the plate and the
    # ramekin") that SAM3 text-match grabs WRONG (verified: SAM3 wrong bowl 19cm off
    # vs vlm_point right bowl 2cm). Re-localizing here with the bare object name
    # throws away that spatial context and re-grabs the wrong look-alike. Only fall
    # back to a fresh localize (which is itself vlm_point-first) when no pixel came.
    pix = args.get("pixel")
    if pix is not None:
        from roborsi.embodied.skills.base._lib.libero._helpers import (
            parse_image_pixel,
        )

        image = state.env.take_snapshot().images.get("head_camera")
        uv = parse_image_pixel(pix, image)
        if uv is None:
            return None
        # The caller already grounded this point on the current frame. Reasking
        # a second VLM identity question rejected valid pixels and sent long
        # episodes back into an expensive find/grasp loop.
        return None if _is_sentinel(uv) else remember_pixel(state, name, uv)
    cached = recall_pixel(state, name)
    if cached is not None:
        return cached
    if name:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            _requires_semantic_pointing,
            localize_precise,
        )

        uv = localize_precise(state, name)
        if uv is None or _is_sentinel(uv):
            return None
        if _requires_semantic_pointing(name):
            image = state.env.take_snapshot().images.get("head_camera")
            if image is None or not _verify_object_pixel(
                state,
                name,
                image,
                (int(uv[0]), int(uv[1])),
            ):
                return None
        return remember_pixel(state, name, uv)
    return None


def _perception_grasp(state, args):
    from roborsi.embodied.skills.base._lib.libero._perception import (
        _wide_hollow_rim_plan,
        execute_base_grip,
        execute_package_side_entry,
        execute_rim_grip,
        execute_topdown,
        grasps_at_pixel,
    )
    env = state.env
    ctrl = LiberoControl(env)
    existing_gap, existing_state = ctrl.read_gripper_state()
    if existing_state is GripperState.HELD:
        snapshot = env.take_snapshot()
        visual = verify_visual_hold(
            env,
            snapshot.images.get("head_camera"),
            holding=True,
        )
        requested_object = str(args.get("object") or "").strip()
        identity_verified = bool(visual.identity_verified)
        held_object = (
            visual.object_name if identity_verified else None
        )
        requested_matches_held = (
            _same_object_name(
                requested_object,
                held_object,
            )
            if held_object is not None
            else None
        )
        return (
            {
                "ok": True,
                "grasped": bool(
                    visual.ok
                    and identity_verified
                    and requested_matches_held
                ),
                "success": bool(
                    visual.ok
                    and identity_verified
                    and requested_matches_held
                ),
                "holding_visual": bool(
                    visual.ok
                    and identity_verified
                    and requested_matches_held
                ),
                "holding": True,
                "visual_verified": visual.ok,
                "identity_verified": identity_verified,
                "do_not_regrasp": True,
                "requested_object": requested_object,
                "held_object": held_object,
                "requested_matches_held": requested_matches_held,
                "gripper_gap": round(float(existing_gap), 4),
                "gripper_state": existing_state.value,
                "visual_hold_recorded": visual.ok,
                "visual_hold_reason": visual.reason,
                "reason": (
                    "regrasp blocked: requested object differs from held object"
                    if requested_matches_held is False
                    else (
                        "regrasp blocked: held object identity was not "
                        "independently verified"
                    )
                    if not identity_verified
                    else "regrasp blocked: gripper already reports held"
                ),
            },
            snapshot,
        )
    clear_visual_hold(env)
    hover = _bounded_arg(args, "hover", 0.10, 0.04, 0.20)
    grasp_z_offset = _bounded_arg(
        args,
        "grasp_z_offset",
        0.0,
        -0.03,
        0.05,
    )
    loc = _locate_pixel(state, args)
    if loc is None:
        return ({"ok": False, "grasped": False,
                 "success": False, "holding_visual": False,
                 "reason": "could not locate the object by vision — call find_pixel(object) and pass pixel=[u, v]"},
                env.take_snapshot())
    if _is_sentinel(loc):
        # Perception returned the image-centre sentinel: NOT a real detection.
        # Refuse the grasp so the VLM re-perceives instead of grasping empty
        # table and looping until budget_exceeded.
        return ({"ok": False, "grasped": False, "success": False,
                 "holding_visual": False, "sentinel": True,
                 "reason": "perception returned the (128,128) centre sentinel (nothing found) — "
                           "re-find_pixel with a more specific query, look() closer, or zoom_in "
                           "before retrying grasp_object with an explicit pixel=[u, v]"},
                env.take_snapshot())
    u, v = loc
    use_hollow_base_grip = _uses_hollow_base_grip(
        args.get("object")
    )
    try:
        pickup_frame = env.coherent_sensor_frame("agentview")
    except Exception:  # noqa: BLE001
        pickup_frame = None
    before_obs = env.take_snapshot()
    before_rgb = (
        pickup_frame.rgb
        if pickup_frame is not None and pickup_frame.valid
        else before_obs.images.get("head_camera")
    )
    requested_object = str(args.get("object") or "").strip()
    cabinet_exit_distance = (
        _CABINET_EXIT_DISTANCE
        if _requires_horizontal_cabinet_exit(requested_object)
        else 0.0
    )
    identity_verified = True
    if requested_object and before_rgb is not None:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            _requires_semantic_pointing,
        )

        if _requires_semantic_pointing(requested_object):
            point_evidence = matching_semantic_point(
                env,
                object_name=requested_object,
                pixel=(u, v),
                current_frame=before_rgb,
            )
            if point_evidence is None:
                try:
                    identity_verified = _verify_object_pixel(
                        state,
                        requested_object,
                        before_rgb,
                        (u, v),
                    )
                except Exception:  # noqa: BLE001
                    identity_verified = False
    before_depth = (
        pickup_frame.depth_m
        if pickup_frame is not None and pickup_frame.valid
        else capture_depth_frame(env)
    )
    grasps, _cloud = grasps_at_pixel(env, u, v, top_k=3)

    def _rim_plan_for(cloud):
        if not _fix_on() or cloud is None or len(cloud) < 30:
            return None
        try:
            base_xy = np.asarray(env.robot_base_pos(), dtype=float)[:2]
        except (AttributeError, TypeError, ValueError, IndexError):
            return None
        return _wide_hollow_rim_plan(
            cloud,
            robot_base_xy=base_xy,
        )

    rim_plan = _rim_plan_for(_cloud) if use_hollow_base_grip else None
    if not grasps and _fix_on() and rim_plan is None:
        # The VLM's pixel yielded a rejected (whole-scene) mask. Rather than bounce
        # back to the VLM — which loops on re-perception until budget_exceeded —
        # self-correct with the detector: re-point via localize_precise and retry.
        name = str(args.get("object") or "").strip()
        if name:
            from roborsi.embodied.skills.base._lib.libero._perception import (
                _requires_semantic_pointing,
                localize_precise,
            )

            loc2 = localize_precise(state, name)
            if loc2 and not _is_sentinel(loc2):
                candidate = int(loc2[0]), int(loc2[1])
                # A detector re-localization is a new perception claim for every
                # object phrase. Never repeat the pixel that already produced no
                # candidates, and require both bounded spatial consistency and
                # fresh public-image identity verification before requesting new
                # grasp geometry.
                candidate_verified = bool(
                    candidate != (int(u), int(v))
                    and _semantic_relocalization_consistent(
                        (int(u), int(v)),
                        candidate,
                    )
                )
                current_rgb = env.take_snapshot().images.get("head_camera")
                if candidate_verified and current_rgb is not None:
                    try:
                        candidate_verified = _verify_object_pixel(
                            state,
                            name,
                            current_rgb,
                            candidate,
                        )
                    except Exception:  # noqa: BLE001
                        candidate_verified = False
                else:
                    candidate_verified = False
                if candidate_verified:
                    g2, c2 = grasps_at_pixel(
                        env,
                        candidate[0],
                        candidate[1],
                        top_k=3,
                    )
                    try:
                        c2_array = np.asarray(c2, dtype=float)
                        cloud_valid = bool(
                            c2_array.ndim == 2
                            and c2_array.shape[0] > 0
                            and c2_array.shape[1] >= 3
                            and np.all(np.isfinite(c2_array[:, :3]))
                        )
                    except (TypeError, ValueError, OverflowError):
                        cloud_valid = False
                    if g2 and cloud_valid:
                        grasps, _cloud, u, v = (
                            g2,
                            c2,
                            candidate[0],
                            candidate[1],
                        )
                        rim_plan = (
                            _rim_plan_for(_cloud)
                            if use_hollow_base_grip
                            else None
                        )
    # Rebind the finally accepted pixel, RGB-D, calibration, identity evidence,
    # and accepted cloud immediately before physical execution. This is required
    # even when the initial pixel was retained, and is especially important when
    # the recovery localization above selected a different candidate. Bracketing
    # grasps_at_pixel with coherent-frame captures proves that no simulator step
    # changed the observation generation while its cloud was produced.
    try:
        rebound_frame = env.coherent_sensor_frame("agentview")
    except Exception:  # noqa: BLE001
        rebound_frame = None
    if rebound_frame is None or not rebound_frame.valid:
        return (
            {
                "ok": False,
                "grasped": False,
                "success": False,
                "holding_visual": False,
                "reason": "coherent pickup RGB-D frame was unavailable",
            },
            env.take_snapshot(),
        )
    rebound_rgb = rebound_frame.rgb
    rebound_identity_verified = True
    if requested_object:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            _requires_semantic_pointing,
        )

        if _requires_semantic_pointing(requested_object):
            point_evidence = matching_semantic_point(
                env,
                object_name=requested_object,
                pixel=(int(u), int(v)),
                current_frame=rebound_rgb,
            )
            if point_evidence is None:
                try:
                    rebound_identity_verified = _verify_object_pixel(
                        state,
                        requested_object,
                        rebound_rgb,
                        (int(u), int(v)),
                    )
                except Exception:  # noqa: BLE001
                    rebound_identity_verified = False
    rebound_grasps, rebound_cloud = grasps_at_pixel(
        env,
        int(u),
        int(v),
        top_k=3,
    )
    try:
        rebound_after = env.coherent_sensor_frame("agentview")
    except Exception:  # noqa: BLE001
        rebound_after = None
    if (
        rebound_after is None
        or not rebound_after.valid
        or rebound_after.frame_token != rebound_frame.frame_token
    ):
        return (
            {
                "ok": False,
                "grasped": False,
                "success": False,
                "holding_visual": False,
                "reason": "pickup observation changed while constructing grasp geometry",
            },
            env.take_snapshot(),
        )
    pickup_frame = rebound_frame
    before_rgb = rebound_frame.rgb
    before_depth = rebound_frame.depth_m
    identity_verified = rebound_identity_verified
    grasps = rebound_grasps
    _cloud = rebound_cloud
    rim_plan = _rim_plan_for(_cloud) if use_hollow_base_grip else None

    if not grasps and not (
        _fix_on()
        and use_hollow_base_grip
        and _cloud is not None
        and len(_cloud) >= 30
    ):
        return ({"ok": False, "grasped": False,
                 "success": False, "holding_visual": False,
                 "reason": "GraspGen found no grasp for the grounded pixel; no accepted same-instance mask is available for safe prompt retries — re-find_pixel or look() closer"},
                env.take_snapshot())
    # A measured wide rim is the strongest geometry available: pinch the rim
    # first, then fall back to the solid base and finally GraspGen.
    p = ee = gap = None
    grasped = False
    # Every close performed by this grasp invocation records this token with its
    # post-close/pre-lift proprioceptive pose. A closure record from an earlier
    # invocation can therefore never authenticate the current pickup.
    grasp_attempt_token = uuid4().hex
    env._libero_grasp_attempt_token = grasp_attempt_token
    env._libero_grasp_closure_pose = None
    grip_state = GripperState.OPEN
    floor_package_grip_attempted = False
    floor_package_grip_attempts = 0
    floor_package_grip_candidate_index = None
    floor_package_z_offset = None
    side_entry_attempted = False
    side_entry_succeeded = False
    side_candidates_attempted = 0
    side_candidate_index = None
    if (
        _fix_on()
        and use_hollow_base_grip
        and rim_plan is not None
    ):
        p, ee, gq = execute_rim_grip(
            env,
            rim_plan,
            hover=hover,
            exit_distance=cabinet_exit_distance,
        )
        gap_raw, grip_state = ctrl.read_gripper_state()
        gap = round(float(gap_raw), 4)
        grasped = grip_state is GripperState.HELD
    if (
        not grasped
        and _fix_on()
        and use_hollow_base_grip
        and rim_plan is not None
        and grip_state is GripperState.CLOSED_EMPTY
    ):
        from roborsi.embodied.skills.base._lib.libero._perception import (
            opposite_rim_plan,
        )

        p, ee, gq = execute_rim_grip(
            env,
            opposite_rim_plan(rim_plan),
            hover=hover,
            exit_distance=cabinet_exit_distance,
        )
        gap_raw, grip_state = ctrl.read_gripper_state()
        gap = round(float(gap_raw), 4)
        grasped = grip_state is GripperState.HELD
    if (
        not grasped
        and
        _fix_on()
        and use_hollow_base_grip
        and _cloud is not None
        and len(_cloud) >= 30
    ):
        p, ee, gq = execute_base_grip(
            env,
            _cloud,
            hover=hover,
            z_offset=grasp_z_offset,
            max_retries=5,
        )
        gap_raw, grip_state = ctrl.read_gripper_state()
        gap = round(float(gap_raw), 4)
        grasped = grip_state is GripperState.HELD
    if (
        not grasped
        and _fix_on()
        and _uses_floor_level_package_grip(requested_object)
        and grasps
        and _cloud is not None
        and len(_cloud) >= 3
    ):
        cloud = np.asarray(_cloud, dtype=float)
        cloud = cloud[np.all(np.isfinite(cloud), axis=1)]
        if len(cloud) >= 3:
            floor_package_z_offset = -float(np.ptp(cloud[:, 2]))
            if np.isfinite(floor_package_z_offset):
                floor_package_grip_attempted = True
                for candidate_index, candidate in enumerate(grasps[:3]):
                    floor_package_grip_attempts += 1
                    p, ee, gq = execute_topdown(
                        env,
                        candidate,
                        cloud=_cloud,
                        hover=hover,
                        z_offset=floor_package_z_offset,
                    )
                    gap_raw, grip_state = ctrl.read_gripper_state()
                    gap = round(float(gap_raw), 4)
                    grasped = grip_state is GripperState.HELD
                    if grasped:
                        floor_package_grip_candidate_index = candidate_index
                        break
    if (
        not grasped
        and _fix_on()
        and _uses_side_entry_package_grip(
            requested_object,
            getattr(state, "_last_identity_package", None),
        )
        and grasps
        and _cloud is not None
        and len(_cloud) >= 10
    ):
        side_entry_attempted = True
        p, ee, gq, side_evidence = execute_package_side_entry(
            env,
            grasps[0],
            _cloud,
            hover=hover,
        )
        side_entry_succeeded = bool(
            side_evidence.get("side_entry_succeeded")
        )
        side_candidates_attempted = int(
            side_evidence.get("side_candidates_attempted") or 0
        )
        side_candidate_index = side_evidence.get("side_candidate_index")
        gap_raw, grip_state = ctrl.read_gripper_state()
        gap = round(float(gap_raw), 4)
        grasped = grip_state is GripperState.HELD
    if not grasped and grasps:
        for candidate in grasps[:3]:
            p, ee, gq = execute_topdown(
                env,
                candidate,
                cloud=_cloud,
                hover=hover,
                z_offset=grasp_z_offset,
            )
            gap_raw, grip_state = ctrl.read_gripper_state()
            gap = round(float(gap_raw), 4)
            grasped = grip_state is GripperState.HELD
            if grasped:
                break
    grasp_pose_pos = None
    grasp_quat = None
    closure = getattr(env, "_libero_grasp_closure_pose", None)
    if grip_state is GripperState.HELD and isinstance(closure, dict) and pickup_frame is not None:
        generation = closure.get("generation")
        if (
            isinstance(generation, (tuple, list))
            and len(generation) == 2
            and closure.get("reset_generation") == pickup_frame.reset_generation
            and generation[0] == pickup_frame.reset_generation
            and closure.get("attempt_token") == grasp_attempt_token
        ):
            grasp_pose_pos = np.asarray(closure.get("position"), dtype=float)
            grasp_quat = np.asarray(closure.get("quaternion_xyzw"), dtype=float)
            if (grasp_pose_pos.shape != (3,) or grasp_quat.shape != (4,)
                    or not np.all(np.isfinite(grasp_pose_pos))
                    or not np.all(np.isfinite(grasp_quat))):
                grasp_pose_pos = None
                grasp_quat = None
    env._libero_grasp_attempt_token = None
    visual_clear_reached = None
    visual_clear_failed = False
    hold_lost_during_clear = False
    if grasped:
        (
            visual_clear_reached,
            holding_after_clear,
            cleared_ee,
        ) = _clear_source_view(env, ctrl)
        if cleared_ee is not None:
            ee = cleared_ee
        gap_raw, grip_state = ctrl.read_gripper_state()
        gap = round(float(gap_raw), 4)
        if not holding_after_clear or grip_state is not GripperState.HELD:
            hold_lost_during_clear = True
            grasped = False
        elif not visual_clear_reached:
            visual_clear_failed = True
            grasped = False
    # A dropped object may have moved or become occluded, and the arm may no
    # longer be in a verified collision-safe approach posture. Fail closed;
    # there is deliberately no automatic re-localize/re-grasp recovery here.
    if side_entry_attempted and not grasped:
        side_entry_succeeded = False
    after_obs = env.take_snapshot()
    source_patch_mad = None
    visual_source_unchanged = False
    visual_source_invalid = False
    visual_hold_recorded = False
    visual_hold_pending = False
    held_object = None
    object_offset_local = None
    release_clearance_hint = 0.04 if side_entry_succeeded else None
    if (
        p is not None
        and grasp_quat is not None
        and _cloud is not None
        and len(_cloud) >= 3
    ):
        if rim_plan is not None:
            object_center = np.array(
                [
                    float(rim_plan["center_xy"][0]),
                    float(rim_plan["center_xy"][1]),
                    float(
                        np.median(
                            np.asarray(_cloud, dtype=float)[:, 2]
                        )
                    ),
                ],
                dtype=float,
            )
        else:
            object_center = np.median(
                np.asarray(_cloud, dtype=float)[:, :3],
                axis=0,
            )
        grasp_origin = (
            grasp_pose_pos
            if grasp_pose_pos is not None
            and grasp_pose_pos.shape == (3,)
            and np.all(np.isfinite(grasp_pose_pos))
            else np.asarray(p, dtype=float)[:3]
        )
        candidate_offset_world = object_center - grasp_origin
        if (
            candidate_offset_world.shape == (3,)
            and np.all(np.isfinite(candidate_offset_world))
            and float(np.linalg.norm(candidate_offset_world)) <= 0.12
        ):
            from scipy.spatial.transform import Rotation

            quat = np.asarray(grasp_quat, dtype=float)
            if quat.shape == (4,) and np.all(np.isfinite(quat)):
                local_offset = Rotation.from_quat(quat).inv().apply(
                    candidate_offset_world
                )
                if np.all(np.isfinite(local_offset)):
                    object_offset_local = tuple(
                        float(value) for value in local_offset
                    )
    pickup_reference = None
    if (
        grasped
        and identity_verified
        and pickup_frame is not None
        and pickup_frame.valid
        and object_offset_local is not None
        and grasp_pose_pos is not None
        and grasp_quat is not None
        and _cloud is not None
    ):
        try:
            from roborsi.embodied.skills.base._lib.libero.instance_hold import (
                build_pickup_reference,
            )

            pickup_reference = build_pickup_reference(
                env,
                frame=pickup_frame,
                object_name=requested_object,
                source_pixel=(int(u), int(v)),
                accepted_cloud=_cloud,
                grasp_position=grasp_pose_pos,
                grasp_quaternion=grasp_quat,
                object_offset_local=object_offset_local,
                identity_verified=True,
            )
        except Exception:  # noqa: BLE001
            pickup_reference = None
    if pickup_reference is None:
        identity_verified = False
    else:
        # Use the centroid of the authenticated, robot-excluded pickup mask in
        # the recorded post-close/pre-lift TCP frame. This keeps transport-time
        # prediction and release geometry tied to the same public RGB-D evidence
        # rather than the potentially broader GraspGen cloud centroid.
        object_offset_local = tuple(
            float(value) for value in pickup_reference.object_offset_local
        )
    if grasped:
        source_patch_mad = _source_patch_motion(
            before_rgb,
            after_obs.images.get("head_camera"),
            (u, v),
        )
        visual_source_invalid = not np.isfinite(source_patch_mad)
        visual_source_unchanged = (
            not visual_source_invalid
            and source_patch_mad <= _SOURCE_STATIC_MAD_MAX
        )
        if visual_source_unchanged or visual_source_invalid:
            grasped = False
        else:
            evidence = record_visual_hold(
                env,
                object_name=str(args.get("object") or "").strip(),
                source_pixel=(u, v),
                before_rgb=before_rgb,
                after_rgb=after_obs.images.get("head_camera"),
                identity_verified=identity_verified,
                object_offset_local=object_offset_local,
                release_clearance_hint=release_clearance_hint,
                pickup_reference=pickup_reference,
            )
            visual_hold_recorded = evidence is not None
            held_object = evidence.object_name if evidence is not None else None
            if not visual_hold_recorded:
                grasped = False
    if visual_clear_failed and grip_state is GripperState.HELD:
        pending = record_pending_visual_hold(
            env,
            object_name=str(args.get("object") or "").strip(),
            source_pixel=(u, v),
            before_rgb=before_rgb,
            before_depth=before_depth,
            identity_verified=identity_verified,
            object_offset_local=object_offset_local,
            release_clearance_hint=release_clearance_hint,
            pickup_reference=pickup_reference,
        )
        visual_hold_pending = pending is not None
    holding = grip_state is GripperState.HELD
    visual_verified = bool(grasped and visual_hold_recorded)
    # Native grasp_holds_actor accepts success or holding_visual.  Expose both
    # only from the same strict conjunction used by grasped: calibrated HELD at
    # the post-clear observation plus successfully recorded visual hold evidence.
    harness_hold = bool(
        holding
        and grasped
        and visual_verified
        and visual_hold_recorded
        and identity_verified
    )
    do_not_regrasp = bool(holding)
    return ({"ok": True, "grasped": grasped,
             "success": harness_hold,
             "holding_visual": harness_hold,
             "backend": "graspgen+sam",
             "grasp_point": (
                 [round(float(x), 4) for x in np.asarray(p).reshape(-1)]
                 if p is not None
                 else []
             ),
             "grasp_pixel": [u, v], "gripper_gap": gap,
             "gripper_state": grip_state.value,
             "holding": holding,
             "visual_verified": visual_verified,
             "identity_verified": bool(
                 visual_hold_recorded and identity_verified
             ),
             "do_not_regrasp": do_not_regrasp,
             "requested_object": requested_object,
             "held_object": held_object,
             "requested_matches_held": (
                 _same_object_name(requested_object, held_object)
                 if held_object is not None
                 else None
             ),
             "source_patch_mad": (
                 round(float(source_patch_mad), 4)
                 if (
                     source_patch_mad is not None
                     and np.isfinite(source_patch_mad)
                 )
                 else None
             ),
             "visual_source_unchanged": visual_source_unchanged,
             "visual_clear_reached": visual_clear_reached,
             "visual_hold_recorded": visual_hold_recorded,
             "visual_hold_pending": visual_hold_pending,
             "floor_package_grip_attempted": floor_package_grip_attempted,
             "floor_package_grip_attempts": floor_package_grip_attempts,
             "floor_package_grip_candidate_index": (
                 floor_package_grip_candidate_index
             ),
             "floor_package_z_offset": (
                 round(float(floor_package_z_offset), 4)
                 if floor_package_z_offset is not None
                 else None
             ),
             "side_entry_attempted": side_entry_attempted,
             "side_entry_succeeded": side_entry_succeeded,
             "side_candidates_attempted": side_candidates_attempted,
             "side_candidate_index": side_candidate_index,
             "release_clearance_hint": release_clearance_hint,
             "object_offset_local": (
                 list(object_offset_local)
                 if object_offset_local is not None
                 else None
             ),
             "reason": (
                 "grasp rejected: could not clear the source view"
                 if visual_clear_failed
                 else "grasp rejected: hold lost while clearing source view"
                 if hold_lost_during_clear
                 else "grasp rejected: source image patch stayed unchanged"
                 if visual_source_unchanged
                 else "grasp rejected: visual source evidence unavailable"
                 if visual_source_invalid
                 else "grasp rejected: visual hold evidence was not recorded"
                 if not grasped and source_patch_mad is not None
                 else None
             ),
             "ee_pos": (
                 [round(float(x), 4) for x in np.asarray(ee).reshape(-1)]
                 if ee is not None
                 else []
             ),
             "note": "perception grasp (no GT). grasped=True means the object is pinched "
                     "AND the source patch changed after lift. Use place_on_surface for "
                     "an exposed support or place_object_in for a container. A SMALL "
                     "gripper_gap (~0.004) can be normal for a bowl foot/rim grip."},
            after_obs)


def dispatch_runtime(state, args: dict[str, Any]):
    return _perception_grasp(state, args)
