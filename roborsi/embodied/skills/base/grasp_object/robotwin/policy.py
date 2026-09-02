"""base.robotwin.grasp_object — Rollout-aligned grasp atomic.

Flow (per Rollout Tier 3 ReKep-style + cuRobo IK precheck):

  1. detect_object(text) via Grounded-SAM         → bbox + mask
  2. predict_grasps_with_mask(mask) via GraspGen  → top-K 6-DoF candidates
  3. cuRobo plan_path (no execute) on each        → filter to IK-ok set
  4. render numbered head-camera overlay          → one panel per IK-ok cand
  5. VLM picks INDEX from the overlay             → \"which one captures {obj}?\"
  6. execute selected: hover → descend → close
  7. verify_holding_visual                        → multi-signal logic check

VLM never returns raw coordinates — it only picks an integer index from a
finite set of physically-feasible (IK-ok) candidates the geometry+kinematics
modules already verified. Failure modes are explicit per stage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


# ── Cross-midline guard (added 2026-06-23) ─────────────────────────────────
# cuRobo IK hangs ~300s on a target that lies on the wrong side of the
# workspace midline for the chosen arm, and the planner worker thread cannot
# be killed → the whole episode's sim state is contaminated. match_blocks_
# bicoord atomic_0 attempts 2 & 3 both died this way: grasp_object(arm='right')
# on a left-side block at x≈-0.10 timed out at 300s and aborted the LH. We
# reject such poses FAST — before any cuRobo call — using the same cheap
# base-x/midline heuristic as base.robotwin.is_reachable (slack 0.05).
_MIDLINE_SLACK = 0.05


def _arm_base_x(impl, arm: str) -> float:
    name = "fl_base_link" if arm == "left" else "fr_base_link"
    art = impl.scene.get_all_articulations()[0]
    link = next((l for l in art.get_links() if l.get_name() == name), None)
    if link is not None:
        return float(link.get_pose().p[0])
    return -0.18 if arm == "left" else 0.18


def _crosses_midline(impl, arm: str, x: float) -> bool:
    """True if a target at world-x `x` is on the far side of the workspace
    midline for `arm` (beyond slack) — cuRobo would hang on it. Mirrors the
    cheap heuristic in base.robotwin.is_reachable."""
    bx = _arm_base_x(impl, arm)
    obx = _arm_base_x(impl, "right" if arm == "left" else "left")
    midline = 0.5 * (bx + obx)
    if arm == "left":
        return x > midline + _MIDLINE_SLACK
    return x < midline - _MIDLINE_SLACK


def _world_to_uv(p, K, extr):
    p_h = np.array([p[0], p[1], p[2], 1.0])
    cam = extr @ p_h
    if cam[2] <= 0:
        return None
    return (int(round(K[0,0]*cam[0]/cam[2] + K[0,2])),
            int(round(K[1,1]*cam[1]/cam[2] + K[1,2])))


def _ik_precheck(impl, pose7: list[float], arm: str) -> tuple[bool, dict]:
    """Reachability gate for a FLANGE target [x,y,z,qw,qx,qy,qz]. Uses ik_feasible —
    config-INDEPENDENT cuRobo IK from a NEUTRAL home qpos. This is RELIABLE, unlike a
    plan from the CURRENT qpos which cuRobo mis-refuses when the arm sits on the wrong
    kinematic branch (flaky across the repeated calls in the precheck loop). The native
    move-chain execution (impl.move) re-plans the actual motion robustly, so a pose
    ik_feasible confirms reachable is enough to admit the candidate."""
    from roborsi.embodied.sim.robotwin.gripper_geom import ik_feasible
    ok, gap = ik_feasible(impl, arm, list(pose7), tol_m=0.03)
    return bool(ok), {"ok": bool(ok), "plan": None, "gap_m": gap,
                      "reason": "" if ok else "ik_feasible=False (unreachable from neutral)"}


def _render_candidate_overlay(rgb: np.ndarray, mask: np.ndarray,
                              candidates: list[dict], K, extr,
                              save_path: Path) -> Path:
    """One numbered marker per candidate's TCP + arrow showing approach."""
    import cv2
    img = rgb.copy()
    if img.dtype != np.uint8:
        img = ((img*255).clip(0,255).astype(np.uint8) if img.max()<=1 else img.astype(np.uint8))
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    # tint mask faintly so VLM sees what's the target object
    img_bgr[mask] = (img_bgr[mask].astype(np.float32) * 0.55 +
                     np.array([0, 255, 255], dtype=np.float32) * 0.45).astype(np.uint8)
    for i, c in enumerate(candidates):
        tcp = c["translation_tcp_world"]
        R = np.asarray(c["rotation_matrix_world"])
        approach = R[:, 2]  # Franka R[:,2]; visualization only
        uv = _world_to_uv(tcp, K, extr)
        if uv is None:
            continue
        col = (0, 165, 255)  # all same color — VLM only judges position+arrow
        cv2.circle(img_bgr, uv, 9, col, -1)
        cv2.circle(img_bgr, uv, 11, (0, 0, 0), 2)
        # approach arrow (5cm step)
        tcp_end = np.asarray(tcp) + approach * 0.05
        uv_end = _world_to_uv(tcp_end, K, extr)
        if uv_end:
            cv2.arrowedLine(img_bgr, uv, uv_end, col, 2, tipLength=0.4)
        cv2.putText(img_bgr, str(i), (uv[0]+12, uv[1]-12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 3)
        cv2.putText(img_bgr, str(i), (uv[0]+12, uv[1]-12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    cv2.imwrite(str(save_path), img_bgr)
    return save_path


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image, _parse_json
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_get_grasp_pose, _do_move_fingertip_to, _do_gripper, _do_verify_holding_visual, _do_move_to_pose
    from roborsi.embodied.sim.robotwin.robotwin_agent import _execute_arm_plan

    arm = str(args.get("arm", "")).lower()
    obj = str(args.get("object", "")).strip()
    top_k = int(args.get("top_k", 30))
    # Candidate-selection strategy for the IK precheck (grasp_object is the shared
    # engine; the agent-facing grasp_top_down / grasp_diverse skills set this):
    #   "top_down" — precheck the steepest downward candidates first. Best for flat/
    #                short objects a straight-down grasp suits.
    #   "diverse"  — precheck an approach-angle-DIVERSE spread so moderate/side grasps
    #                get tried too. Best for tall cylinders (can, bottle, roller) whose
    #                reachable grasp is a side grasp, and as the robust default.
    strategy = str(args.get("strategy", "diverse")).lower()
    # When the GraspGen cloud is symmetry-COMPLETED (grasp_diverse), its centroid is
    # already on the object axis, so the per-candidate DEPTH-centering below must be
    # skipped — otherwise it double-pushes the TCP past the center and all grasps IK-fail.
    _complete_sym = bool(args.get("complete_symmetric", False))
    # Optional target pixel (u,v) disambiguating WHICH instance to grasp when the
    # object name matches several regions (e.g. "can" also grounds to the pot in
    # move_can_pot). When given, both the pre-cloud and GraspGen mask are taken from
    # the detection region at (u,v) — not the top-score region. The Engineer gets
    # this pixel from find_pixel / its own visual localization.
    _u = args.get("u")
    _v = args.get("v")
    target_uv = (int(_u), int(_v)) if _u is not None and _v is not None else None

    if arm not in {"left", "right", "auto"}:
        return ({"ok": False, "reason": f"arm must be left/right/auto, got {arm!r}"},
                _snapshot(state.env))
    if not obj:
        return ({"ok": False, "reason": "object name required"}, _snapshot(state.env))

    impl = state.env._impl

    # 1+2. SAM mask + GraspGen candidates (delegated to _do_get_grasp_pose
    # which already uses the Grounded-SAM masked path).
    # z_max clamps the unprojected cloud to exclude robot-arm/background pixels
    # that SAM masks sometimes bleed into (they project to Z=0.85+ even though
    # the actual object top is at Z~0.76).
    TABLE_Z_APPROX = 0.72  # scene-level constant; re-used in Z correction below
    HOVER_Z_LIFT_M = 0.08  # hover this high above grasp (Z-axis approach, avoids singularity)

    # Build the object point cloud BEFORE calling GraspGen so we can pass a
    # tight z_max. When the robot arm overlaps the object in the camera view,
    # SAM masks bleed into arm pixels (Z≈0.836). With z_max=TABLE+0.30=1.02
    # those arm pixels reach GraspGen and cause all candidates to have upward
    # approach (arm visible at the top of the cloud → GraspGen thinks it should
    # approach from below). Fix: 80th-percentile Z of the object cloud → use
    # that as z_max so arm pixels (minority, high Z) are excluded.
    impl._update_render(); impl.cameras.update_picture()
    _rgb_pre = impl.cameras.get_rgb()["head_camera"]["rgb"]
    _depth_pre = impl.cameras.get_depth()["head_camera"]["depth"]
    _cfg_pre = impl.cameras.get_config()["head_camera"]
    if _rgb_pre.dtype != np.uint8:
        _rgb_pre = ((_rgb_pre*255).clip(0,255).astype(np.uint8)
                    if _rgb_pre.max() <= 1 else _rgb_pre.astype(np.uint8))
    _K = np.asarray(_cfg_pre["intrinsic_cv"])
    _extr = np.asarray(_cfg_pre["extrinsic_cv"])
    if _extr.shape == (3, 4):
        _extr_h = np.eye(4); _extr_h[:3] = _extr
    else:
        _extr_h = _extr
    _cam2world = np.linalg.inv(_extr_h)
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect as _gs_detect
    _dets_pre = _gs_detect(np.asarray(_rgb_pre), obj, top_k=3 if target_uv else 1)
    _world = np.empty((0, 3))
    z_max_graspgen = TABLE_Z_APPROX + 0.30  # fallback: no SAM hit
    if _dets_pre:
        # Pick the detection region at the target pixel (disambiguates can from pot);
        # fall back to the top-score region when no (u,v) hint was given.
        if target_uv is not None:
            _tu, _tv = target_uv
            _pre = next((d for d in _dets_pre
                         if 0 <= _tv < d.mask.shape[0] and 0 <= _tu < d.mask.shape[1]
                         and d.mask[_tv, _tu]), None)
            if _pre is None:
                _pre = min(_dets_pre, key=lambda d: (d.centroid[0] - _tu) ** 2
                           + (d.centroid[1] - _tv) ** 2)
        else:
            _pre = _dets_pre[0]
        _ys, _xs = np.where(_pre.mask)
        _zc = _depth_pre[_ys, _xs].astype(np.float64) / 1000.0
        _v = _zc > 0
        _ys, _xs, _zc = _ys[_v], _xs[_v], _zc[_v]
        _xc = (_xs - _K[0, 2]) * _zc / _K[0, 0]
        _yc = (_ys - _K[1, 2]) * _zc / _K[1, 1]
        _ch = np.stack([_xc, _yc, _zc, np.ones_like(_zc)], axis=1)
        _world_loose = (_cam2world @ _ch.T).T[:, :3]
        _world_loose = _world_loose[(_world_loose[:, 2] >= TABLE_Z_APPROX - 0.05) &
                                    (_world_loose[:, 2] <= TABLE_Z_APPROX + 0.30)]
        if len(_world_loose) >= 10:
            # 80th-percentile Z: arm pixels are a minority (high-Z outliers),
            # so p80 lands at or just above the object top, below the arm.
            z_p80 = float(np.percentile(_world_loose[:, 2], 80))
            z_max_graspgen = max(z_p80 + 0.02, TABLE_Z_APPROX + 0.05)
            _world = _world_loose[_world_loose[:, 2] <= z_max_graspgen]
        else:
            _world = _world_loose

    print(f"  [DBG] cloud: loose={len(_world_loose) if _dets_pre else 0} "
          f"tight={len(_world)} z_max_graspgen={z_max_graspgen:.3f} "
          f"z_world_range=[{_world[:,2].min():.3f},{_world[:,2].max():.3f}]"
          if len(_world) > 0 else f"  [DBG] z_max_graspgen={z_max_graspgen:.3f} _world empty")

    # CROSS-MIDLINE GUARD (added): fail FAST before any cuRobo call when the
    # object centroid is on the far side of the midline for the chosen arm —
    # otherwise cuRobo IK hangs ~300s on the infeasible pose and the un-killable
    # worker thread contaminates the whole episode's sim state (match_blocks_
    # bicoord attempts 2 & 3). Only applies to a concrete arm; 'auto' is
    # filtered per-arm inside the IK precheck loop below.
    if arm in ("left", "right") and len(_world) >= 10:
        _cx = float(np.median(_world[:, 0]))
        if _crosses_midline(impl, arm, _cx):
            _other = "right" if arm == "left" else "left"
            if not _crosses_midline(impl, _other, _cx):
                # "如果不行就换臂": the requested arm can't reach across the midline,
                # but the other arm can — switch to it instead of failing.
                print(f"  [DBG] arm-switch(pre-grasp): {arm} crosses midline at "
                      f"x={_cx:.3f} -> using {_other}")
                arm = _other
            else:
                return ({"ok": False, "stage": "cross_midline_guard",
                         "reason": (f"object centroid x={_cx:.3f} is across the workspace "
                                    "midline for BOTH arms — un-graspable from either side "
                                    "without handing it across first."),
                         "object_centroid_x": round(_cx, 4), "arm": arm},
                        _snapshot(state.env))

    _gp_args = {"object": obj, "top_k": top_k, "z_max": z_max_graspgen,
                "complete_symmetric": bool(args.get("complete_symmetric", False))}
    if target_uv is not None:
        _gp_args["u"], _gp_args["v"] = target_uv   # disambiguate region (can vs pot)
    gp_res, _ = _do_get_grasp_pose(state, _gp_args)
    if not gp_res.get("ok"):
        return ({"ok": False, "reason": f"get_grasp_pose failed: {gp_res.get('reason')}"},
                _snapshot(state.env))
    candidates = gp_res.get("candidates") or []
    print(f"  [DBG] GraspGen returned {len(candidates)} candidates")
    if not candidates:
        return ({"ok": False, "reason": "no candidates from GraspGen"}, _snapshot(state.env))

    # 2.5 Geometric sanity filter: TCP must be INSIDE the SAM mask's
    # 3D point cloud bbox. GraspGen sometimes returns \"best grasp = approach
    # the object end from outside\" which puts TCP in empty air past the
    # object's edge. Reuse the already-built _world cloud (arm pixels excluded).
    if len(_world) < 10:
        candidates = []
        bbox_min = bbox_max = np.zeros(3)
    else:
        bbox_min = _world.min(axis=0) - 0.05
        bbox_max = _world.max(axis=0) + 0.05
    in_bbox = []
    for c in candidates:
        tcp = np.asarray(c["translation_tcp_world"])
        R_world = np.asarray(c["rotation_matrix_world"])
        R_ee = np.asarray(c.get("rotation_matrix_ee", c["rotation_matrix_world"]))
        approach = R_world[:, 2]
        # TCP Z correction for horizontal grasps: head camera sees the object from
        # above, so GraspGen sometimes places TCP too high (near cloud top) or too
        # low. Target = mid-height of the object cloud (cloud_z_min to cloud_z_top).
        # Correction is bidirectional — bring TCP toward the object center in Z.
        if abs(float(approach[2])) < 0.4:   # horizontal approach
            cloud_z_top_90 = float(np.percentile(_world[:, 2], 90))
            cloud_z_bot_10 = float(np.percentile(_world[:, 2], 10))
            target_z = (cloud_z_bot_10 + cloud_z_top_90) * 0.5
            z_correction = float(tcp[2]) - target_z  # + → too high, − → too low
            if abs(z_correction) > 0.005:
                tcp = tcp.copy(); tcp[2] -= z_correction
                c["translation_tcp_world"] = tcp.tolist()
                c["translation_world"] = tcp.tolist()
                _base = np.asarray(c.get("translation_base_world", tcp))
                _base = _base.copy(); _base[2] -= z_correction
                c["translation_base_world"] = _base.tolist()
            # DEPTH correction: the single-view cloud is only the object's camera-FACING
            # shell, so its centroid (and GraspGen's TCP) sits ~radius in FRONT of the
            # true center. A side grasp closing there meets in front of the object body
            # and shoves it (measured ~5cm on the move_can_pot can). Push the TCP along
            # the camera view ray (AWAY from the camera, into the object) by the estimated
            # radius so the fingers close on the center. Radius ≈ half the cloud's lateral
            # spread perpendicular to the view ray. View ray (not the GraspGen approach)
            # is used so the push direction is always "deeper", independent of grasp roll.
            _cam_pos = (_cam2world @ np.array([0.0, 0.0, 0.0, 1.0]))[:3]
            _view = np.asarray(tcp, float) - _cam_pos
            _view[2] = 0.0
            if np.linalg.norm(_view) > 1e-6:
                _view = _view / np.linalg.norm(_view)
                _lat = np.array([-_view[1], _view[0], 0.0])   # ⊥ view ray, horizontal
                _rel = _world - _world.mean(axis=0)
                _lat_extent = float(np.percentile(_rel @ _lat, 90)
                                    - np.percentile(_rel @ _lat, 10))
                _radius = float(np.clip(0.5 * _lat_extent, 0.0, 0.05))
                if _radius > 0.005 and not _complete_sym:
                    tcp = np.asarray(tcp, float).copy(); tcp[:2] += _view[:2] * _radius
                    c["translation_tcp_world"] = tcp.tolist()
                    c["translation_world"] = tcp.tolist()
                    _b = np.asarray(c.get("translation_base_world", tcp)).copy()
                    _b[:2] += _view[:2] * _radius
                    c["translation_base_world"] = _b.tolist()
                    print(f"  [DBG] depth-center: TCP += view*{_radius*100:.1f}cm "
                          f"(front-shell→center)")
        # 1) TCP must be CLOSE to the actual point cloud (not just bbox).
        tcp_to_cloud_min = float(np.linalg.norm(_world - tcp, axis=1).min())
        # 2) TCP Z must be within the object's Z extent (+2cm slack).
        #    Use percentile-based Z bounds so robot-arm outlier pixels
        #    (projected to Z=0.83+) don't inflate the accepted Z range.
        cloud_z_min = float(np.percentile(_world[:, 2], 5))
        cloud_z_max = float(np.percentile(_world[:, 2], 95))
        tcp_in_z_range = (cloud_z_min - 0.02 <= float(tcp[2]) <= cloud_z_max + 0.02)
        # 3) Straddle check: physical aloha jaw direction = R_ee[:,1] (fl_link6 +Y).
        #    GraspGen jaw (R_world[:,0]) maps to aloha +Y (R_ee[:,1]).
        jaw = R_ee[:, 1]
        half_spread = 0.072   # aloha jaw fully open: fl_link7/8 at ±0.072m Y
        tipA = tcp + jaw * half_spread
        tipB = tcp - jaw * half_spread
        cloud_centroid = _world.mean(axis=0)
        sa = float(np.dot(tipA - cloud_centroid, jaw))
        sb = float(np.dot(tipB - cloud_centroid, jaw))
        straddles = (sa * sb < 0)   # opposite signs = jaw crosses object
        cloud_close = (tcp_to_cloud_min < 0.04)   # within 4cm of any cloud point
        # 4) Hover feasibility: hover = tcp + [0, 0, HOVER_Z_LIFT_M] (straight up).
        # Z-up hover is always above the grasp, so the only failure case is if the
        # flange would still be below the table (impossible since tcp_z >= table).
        hover_tcp_z = float(tcp[2]) + HOVER_Z_LIFT_M
        hover_flange_z = hover_tcp_z - 0.1556 * float(R_ee[2, 0])
        hover_feasible = hover_flange_z >= TABLE_Z_APPROX - 0.05
        # 5) Top-surface collision: for a steep downward approach (approach_z < -0.3)
        # where the jaw does NOT straddle the object, the finger tips press down onto
        # the object's top face to reach the TCP, which knocks a closed-top object
        # (sauce can) sideways. BUT when the jaw straddles the object (straddles=True),
        # the fingers descend ALONGSIDE it on either side — they never touch the top —
        # so a straddling top-down grasp is safe. Gating on `not straddles` stops this
        # filter from wrongly rejecting the (abundant) straddling top-down candidates;
        # the cuRobo IK+collision precheck below is the real feasibility gate.
        top_surface_z = cloud_z_max  # 95th-percentile Z ≈ object top face
        top_hit = (float(approach[2]) < -0.3 and float(tcp[2]) < top_surface_z
                   and not straddles)
        # A grasp whose approach axis points UP (approach_z > 0.2) reaches the object
        # from BELOW — impossible for anything resting on the table (the table blocks
        # the gripper). GraspGen emits these when the masked cloud is one-sided; drop
        # them so the diverse precheck spread isn't wasted on un-executable poses.
        approach_from_below = float(approach[2]) > 0.2
        approach_safe = not top_hit and not approach_from_below
        if cloud_close and straddles and tcp_in_z_range and hover_feasible and approach_safe:
            in_bbox.append(c)
        else:
            c["filter_reason"] = (f"TCP_to_cloud_min={tcp_to_cloud_min*100:.1f}cm "
                                   f"(need<4) straddles={straddles} "
                                   f"tcp_in_z=[{cloud_z_min:.2f},{cloud_z_max:.2f}]+/-2cm={tcp_in_z_range} "
                                   f"(jaw signed dists {sa:.3f}/{sb:.3f}) "
                                   f"hover_flange_z={hover_flange_z:.2f}>={TABLE_Z_APPROX-0.05:.2f}={hover_feasible} "
                                   f"top_hit={top_hit} from_below={approach_from_below}")
    if not in_bbox:
        return ({"ok": False,
                 "reason": f"all {len(candidates)} GraspGen TCPs outside the object bbox; "
                           "GraspGen wants to grip from outside the body but no point of "
                           "approach lands ON the object",
                 "n_total": len(candidates),
                 "object_bbox": [bbox_min.tolist(), bbox_max.tolist()]},
                _snapshot(state.env))
    candidates = in_bbox
    # Sort by approach Z (col2 of rotation_matrix_world) ascending, i.e. steepest
    # DOWNWARD grasp first. This is the canonical order; the `strategy` below decides
    # whether the precheck takes the steepest few (top_down) or an angle-diverse spread.
    candidates.sort(key=lambda c: float(np.asarray(c["rotation_matrix_world"])[2, 2]))
    print(f"  [DBG] after bbox/hover filter: {len(candidates)} candidates remain"
          f"  (rejected by filter_reason: "
          + str(sum(1 for c in gp_res.get('candidates',[]) if 'filter_reason' in c)) + ")")
    for c in gp_res.get('candidates', []):
        if 'filter_reason' in c:
            tcp_c = c.get('translation_tcp_world', [0,0,0])
            R_w = np.asarray(c.get('rotation_matrix_world', np.eye(3)))
            apz = float(R_w[2, 2])
            print(f"    [FILTERED] tcp_z={tcp_c[2]:.3f} approach_z={apz:.2f} "
                  f"| {c['filter_reason'][:200]}")

    # 3. IK precheck — for each candidate, verify BOTH hover and grasp
    # are reachable by cuRobo. Checking only the grasp pose is insufficient:
    # a candidate that passes grasp IK but fails hover IK will still execute a
    # partial plan (moved=True, reached=False) that physically displaces the object.
    # "如果不行就换臂": for a specific arm, try it FIRST but fall back to the other
    # arm per-candidate when the requested one can't reach (cross-midline is skipped
    # fast inside the loop, IK-fail falls through to the next arm). RoboTwin scenes are
    # solvable by construction, so a candidate the left arm can't reach the right often can.
    if arm == "auto":
        arms_to_check = ["left", "right"]
    else:
        arms_to_check = [arm, "right" if arm == "left" else "left"]
    ik_ok: list[dict[str, Any]] = []
    from roborsi.embodied.sim.robotwin.gripper_geom import flange_from_tcp
    # Cap execution attempts: repeated gripper contacts physically displace the object.
    # A thin cylinder often needs several tries before one grasp captures, so allow more.
    MAX_EXEC_ATTEMPTS = 6
    # ik_feasible resets to a neutral qpos on every call, so there is NO cuRobo warm-start
    # degradation across the loop (the old plan-from-current cap of 5 no longer applies).
    # Precheck many candidates so the reachable ones are found — reachable grasps are a
    # minority for a standing can, and a spread of just 5 often misses all of them.
    MAX_IK_PRECHECK = 40
    # Pick which candidates to IK-precheck, per `strategy`. `candidates` is sorted by
    # approach_z (steepest downward first).
    #   top_down: the steepest few — a straight-down grasp for flat/short objects.
    #   diverse : an approach-angle spread. GraspGen often returns a cluster of near-
    #             identical VERTICAL grasps (approach_z≈-1.0) the aloha wrist cannot
    #             reach at a table reach (hover AND grasp IK both fail), which would burn
    #             the whole 5-slot budget on unreachable poses while the moderate/side
    #             grasps a cylinder needs (per the successful plan: "preferring a
    #             side-grasp") sit further down and never get checked. linspace-sampling
    #             the sorted list spreads the precheck across vertical→side so the
    #             reachable ones are tried.
    if strategy == "top_down":
        precheck_candidates = candidates[:MAX_IK_PRECHECK]
    elif len(candidates) > MAX_IK_PRECHECK:
        _order = np.linspace(0, len(candidates) - 1, MAX_IK_PRECHECK).round().astype(int)
        precheck_candidates = [candidates[i] for i in dict.fromkeys(_order.tolist())]
    else:
        precheck_candidates = candidates
    print(f"  [DBG] strategy={strategy} precheck approach_z: "
          f"{[round(float(np.asarray(c['rotation_matrix_world'])[2,2]),2) for c in precheck_candidates]}")
    for c in precheck_candidates:
        tcp = np.asarray(c["translation_tcp_world"])
        R_world = np.asarray(c["rotation_matrix_world"])   # GraspGen axes (jaw=col0, approach=col2)
        R_ee = np.asarray(c.get("rotation_matrix_ee", c["rotation_matrix_world"]))  # aloha fl_link6
        approach = R_world[:, 2]   # GraspGen approach direction
        quat = c["quat_wxyz_world"]
        # Hover = tcp + [0, 0, HOVER_Z_LIFT_M]: straight up above the grasp.
        # Approach-direction hover (tcp - back*approach) puts the arm near kinematic
        # singularity when approach is horizontal (approach_z≈0), causing 10+cm
        # trajectory tracking errors. Z-up hover has no singularity issue and naturally
        # pairs with a straight-line constrained Z descent to the grasp.
        hover_tcp = tcp.copy()
        hover_tcp[2] = tcp[2] + HOVER_Z_LIFT_M
        for a in arms_to_check:
            # CROSS-MIDLINE GUARD (added): never hand cuRobo a candidate on the
            # far side of the midline for arm `a` — it hangs ~300s and the
            # un-killable worker contaminates the sim. Skip fast instead.
            if _crosses_midline(impl, a, float(tcp[0])):
                print(f"  [DBG] skip(cross-midline): arm={a} tcp_x={float(tcp[0]):.3f}")
                continue
            flange_grasp = flange_from_tcp(impl, tcp, R_ee, a).tolist()
            # PRE-GRASP flange: 6cm behind the grasp along the approach — this is where the
            # native move-chain execution starts its straight move-in, so gate reachability
            # on it (not the old Z-up hover, which the native path no longer uses).
            flange_hover = (np.asarray(flange_grasp) - approach * 0.06).tolist()
            ok_grasp, info_grasp = _ik_precheck(impl, [*flange_grasp, *quat], a)
            if not ok_grasp:
                print(f"  [DBG] IK-fail(grasp): arm={a} tcp={np.round(tcp,3).tolist()} "
                      f"approach_z={float(approach[2]):.2f} reason={info_grasp.get('reason','')[:80]}")
                continue
            ok_hover, info_hover = _ik_precheck(impl, [*flange_hover, *quat], a)
            if not ok_hover:
                print(f"  [DBG] IK-fail(pre-grasp): arm={a} "
                      f"tcp={np.round(tcp,3).tolist()} reason={info_hover.get('reason','')[:80]}")
                continue
            c["ik_arm"] = a
            c["flange_world_for_arm"] = flange_grasp
            c["hover_tcp_world"] = hover_tcp.tolist()
            c["hover_flange_world"] = flange_hover
            c["ik_predicted_gap_m"] = info_grasp.get("gap_m")
            c["hover_plan"] = info_hover.get("plan")   # cache for replay at execution
            c["grasp_plan"] = info_grasp.get("plan")   # cache for replay at execution
            # Plan HOVER→GRASP descend using last_qpos parameter to avoid
            # art.set_qpos teleportation. Use constraint_pose=[1,1,1,0,0,0] for
            # straight-line XY-fixed Z descent (matches Z-up hover strategy perfectly).
            # Only when a hover PLAN exists (plan-from-current succeeded); if the pose was
            # admitted via ik_feasible (reachable but plan-from-current was flaky), skip —
            # execution re-plans the descend fresh from the actual post-hover state.
            if info_hover.get("plan") is not None:
                hover_terminal = np.asarray(info_hover["plan"]["position"][-1])
                art = impl.scene.get_all_articulations()[0]
                _curr_qpos = np.array(art.get_qpos(), copy=True)
                _active_jnames = (impl.robot.left_planner.active_joints_name if a == "left"
                                  else impl.robot.right_planner.active_joints_name)
                _all_jnames = [j.get_name() for j in art.get_active_joints()]
                _n2i = {n: i for i, n in enumerate(_all_jnames)}
                _arm_idx = [_n2i[n] for n in _active_jnames if n in _n2i]
                _full_hover_qpos = _curr_qpos.copy()
                if len(_arm_idx) == len(hover_terminal):
                    _full_hover_qpos[_arm_idx] = hover_terminal
                plan_fn = (impl.robot.left_plan_path if a == "left"
                           else impl.robot.right_plan_path)
                descend_raw = plan_fn(list(flange_grasp) + list(quat),
                                      last_qpos=_full_hover_qpos,
                                      constraint_pose=[1, 1, 1, 0, 0, 0])
                ok_descend = (descend_raw.get("status") == "Success" and
                              len(descend_raw.get("position", [])) > 0)
                c["descend_plan"] = descend_raw if ok_descend else None
            else:
                c["descend_plan"] = None
                ok_descend = False   # execution will re-plan the descend from actual state
            print(f"  [DBG] IK-ok: arm={a} tcp={np.round(tcp,3).tolist()} "
                  f"approach_z={float(approach[2]):.2f} hover_tcp_z={float(hover_tcp[2]):.3f} "
                  f"flange_grasp_z={flange_grasp[2]:.3f} gap_m={info_grasp.get('gap_m')} "
                  f"descend_ok={ok_descend}")
            ik_ok.append(c)
            break
        if len(ik_ok) >= MAX_EXEC_ATTEMPTS:
            break  # have enough verified candidates for execution
    if not ik_ok:
        return ({"ok": False, "reason": f"all {len(candidates)} GraspGen candidates "
                 "failed cuRobo IK precheck (hover+grasp)", "n_total": len(candidates)},
                _snapshot(state.env))

    # 4. Render numbered overlay for IK-ok candidates.
    impl._update_render(); impl.cameras.update_picture()
    rgb = impl.cameras.get_rgb()["head_camera"]["rgb"]
    cfg = impl.cameras.get_config()["head_camera"]
    K = np.asarray(cfg["intrinsic_cv"]); extr = np.asarray(cfg["extrinsic_cv"])
    if extr.shape == (3, 4):
        ext_h = np.eye(4); ext_h[:3] = extr; extr_for_proj = ext_h[:3]
    else:
        extr_for_proj = extr[:3]

    # SAM mask was computed inside _do_get_grasp_pose; re-detect once for overlay.
    from roborsi.embodied.skills.base.detect_object.robotwin.policy import detect
    dets = detect(np.asarray(rgb), obj, top_k=2)
    mask = dets[0].mask if dets else np.zeros(rgb.shape[:2], dtype=bool)

    workdir = state.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    overlay_path = workdir / f"grasp_candidates_{len(list(workdir.glob('grasp_candidates_*.jpg'))):03d}.jpg"
    _render_candidate_overlay(np.asarray(rgb), mask, ik_ok, K, extr_for_proj, overlay_path)

    # 5. VLM picks index. Constrained: integer 0..N-1.
    n = len(ik_ok)
    system = (
        "You select an INDEX from a numbered candidate set. The image shows "
        "a robot scene with a target object highlighted in YELLOW (mask) and "
        "N orange-numbered grasp candidates overlaid (dot = where the gripper "
        "fingertips will close, arrow = approach direction). Pick the index "
        "of the grasp most likely to PHYSICALLY CAPTURE the object — i.e. the "
        "fingertips close on the object's body, not on its top edge or in "
        "empty space beside it. Reply with a single JSON object: "
        "{\"index\": int, \"reason\": \"<one short clause>\"}."
    )
    user = (f"Object to grasp: {obj}. Candidates: 0..{n-1}. "
            f"Pick the index of the grasp most likely to capture the object body.")
    raw = _call_vlm_image(DEFAULT_MODEL, system, user, overlay_path)
    parsed = _parse_json(raw) or {}
    idx = parsed.get("index", 0)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0
    if not (0 <= idx < n):
        idx = 0
    chosen_for_log = ik_ok[idx]
    vlm_reason = str(parsed.get("reason", ""))[:200]

    # 6. Try VLM-picked first, then fall back to other IK-ok candidates.
    try_order = ([idx] + [i for i in range(n) if i != idx])[:MAX_EXEC_ATTEMPTS]
    attempts: list[dict[str, Any]] = []
    for try_idx in try_order:
        chosen = ik_ok[try_idx]
        chosen_arm = chosen["ik_arm"]
        tcp = chosen["translation_tcp_world"]
        quat = chosen["quat_wxyz_world"]
        hover = chosen["hover_tcp_world"]   # pre-IK-verified, stored during precheck

        # Return arm to HOME between attempts — ensures each hover starts from
        # a known position so the fresh hover plan is valid.
        if try_idx != try_order[0]:
            from envs.utils.action import ArmTag as _AT
            impl.plan_success = True
            impl.move((_AT(chosen_arm),
                       [type("A", (), {"arm_tag": chosen_arm, "action": "move",
                                       "target_pose": (impl.robot.left_original_pose
                                                       if chosen_arm == "left" else
                                                       impl.robot.right_original_pose),
                                       "args": {}})()]))

        _do_gripper(state, {"arm": chosen_arm, "action": "open"})
        from roborsi.embodied.sim.robotwin.gripper_geom import tcp_from_flange
        _R_ee = np.asarray(chosen.get("rotation_matrix_ee", chosen.get("rotation_matrix_world")))
        # NATIVE move-chain grasp — matches RoboTwin's working grasp_actor mechanic, replacing
        # the old Z-up-hover + vertical-descend (which failed to PLAN side grasps from HOME and
        # knocked the standing object). Move to a PRE-GRASP 6cm behind the grasp along the
        # approach axis, then a straight move IN to the grasp, then physically close. Each
        # _do_move_to_pose == impl.move([Action "move"]) — plans robustly in the FULL collision
        # world; close is a physical finger close (no attach). Pure grasp of a perceived pose.
        _flange_grasp = np.asarray(chosen["flange_world_for_arm"], dtype=float)
        _approach = np.asarray(chosen["rotation_matrix_world"])[:, 2]
        _pre = (_flange_grasp - _approach * 0.06).tolist()
        p_res, _ = _do_move_to_pose(state, {"arm": chosen_arm, "x": _pre[0], "y": _pre[1],
                                            "z": _pre[2], "quat": list(quat)})
        g_res, _ = _do_move_to_pose(state, {"arm": chosen_arm,
                                            "x": float(_flange_grasp[0]),
                                            "y": float(_flange_grasp[1]),
                                            "z": float(_flange_grasp[2]), "quat": list(quat)})
        _ee_d = (impl.robot.get_left_ee_pose() if chosen_arm == "left"
                 else impl.robot.get_right_ee_pose())
        _tcp_actual_d = tcp_from_flange(impl, np.array(_ee_d[:3]), _R_ee, chosen_arm)
        _desc_err = float(np.linalg.norm(_tcp_actual_d - np.asarray(tcp)))
        d_res = g_res
        attempt = {"index": try_idx, "arm": chosen_arm, "score": chosen.get("score"),
                   "pre_ok": bool(p_res.get("ok")), "descend_ok": bool(g_res.get("ok")),
                   "descend_err_m": round(_desc_err, 4)}
        print(f"  [DBG] native move-in: pre_ok={p_res.get('ok')} grasp_ok={g_res.get('ok')} "
              f"tcp_err={_desc_err*100:.1f}cm")
        if not g_res.get("ok"):
            attempts.append(attempt)
            continue
        _do_gripper(state, {"arm": chosen_arm, "action": "close"})

        # Post-grasp: a PURE straight-up lift of the grasp flange via the native move,
        # then verify. A firm grasp rises with the gripper (isolated: +6.6cm HELD); a
        # missed grasp barely moves the object (small Z lift, no sideways sweep), so a
        # FAILED attempt does NOT displace the can and the next candidate can still aim
        # at it. (The old y=-0.1 move_by_displacement shoved a not-yet-gripped can aside,
        # breaking every subsequent attempt.)
        _lift = np.asarray(chosen["flange_world_for_arm"], dtype=float)
        _lift[2] += 0.08
        _do_move_to_pose(state, {"arm": chosen_arm, "x": float(_lift[0]),
                                 "y": float(_lift[1]), "z": float(_lift[2]),
                                 "quat": list(quat)})

        v_res, _ = _do_verify_holding_visual(state, {"arm": chosen_arm, "object": obj,
                                                       "lift_first": False,
                                                       "table_z": TABLE_Z_APPROX})
        attempt["holding_visual"] = bool(v_res.get("holding_visual"))
        attempts.append(attempt)
        if v_res.get("holding_visual"):
            return ({"ok": True,
                     "stage": "verify_passed",
                     "selected_index": try_idx,
                     "vlm_picked_index": idx,
                     "n_ik_ok": n, "n_total": len(candidates),
                     "n_attempts": len(attempts),
                     "chosen_arm": chosen_arm,
                     "chosen_score": chosen.get("score"),
                     "chosen_approach_z": chosen.get("approach_z"),
                     "chosen_tcp": tcp,
                     "vlm_reason": vlm_reason,
                     "overlay": str(overlay_path),
                     "attempts": attempts,
                     "verify": v_res,
                     "object": obj, "arm": chosen_arm,
                     "note": ("Real grasp confirmed. Tried " + str(len(attempts)) +
                              " candidate(s); VLM picked index " + str(idx) +
                              " first" + (", succeeded on that one" if try_idx == idx
                                           else f", fell back to index {try_idx}"))},
                    _snapshot(state.env))
        # gripper close happened but verify failed — open and try next
        _do_gripper(state, {"arm": chosen_arm, "action": "open"})

    # all attempts exhausted
    return ({"ok": False, "stage": "all_candidates_exhausted",
             "reason": f"Tried {len(attempts)} of {n} IK-ok candidates, "
                       "none captured the object physically. "
                       "VLM's first pick failed; fallbacks also failed.",
             "selected_index": idx, "n_ik_ok": n, "n_total": len(candidates),
             "vlm_reason": vlm_reason,
             "overlay": str(overlay_path),
             "attempts": attempts,
             "object": obj},
            _snapshot(state.env))


def run(env=None, **_: Any):
    raise RuntimeError(
        "grasp_object runs inside the rollout tool loop; call via VLM tool dispatch.")
