"""RoboTwin-specific tool helpers for the universal rollout loop.

The backend-agnostic driver — ``run_rollout``, the dispatcher, ``DispatchContext``,
and the snapshot/success wrappers — lives in
``roborsi.embodied.agent_loop.rollout`` and drives any backend through the
``Env`` seam. This module keeps only the RoboTwin-SPECIFIC geometry + arm-control
helpers that the ``_do_*`` tool handlers compose (cuRobo trajectory replay, pixel
unprojection, top-down grasp synthesis, contact-based held-actor discovery), the
RoboTwin tool registry (``_ensure_registry``), and a re-export of the 22 ``_do_*``
implementations from ``robotwin_tools``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from roborsi.embodied.agent_loop.env import Observation
# _execute_arm_plan takes a DispatchContext and snapshots via the Env seam.
from roborsi.embodied.agent_loop.rollout import DispatchContext, _snapshot


_BASE_SKILL_REGISTRY: dict[str, Any] | None = None


def _ensure_registry() -> dict[str, Any]:
    """RoboTwin tool registry: name → ``_do_<name>``, scanned once from
    ``robotwin_tools``. ``RoboTwinEnv.tool_handlers()`` delegates here so the
    universal loop dispatches RoboTwin tools; codeact_runtime / exec_python /
    register_skill also introspect it directly (env-less) to enumerate the
    wired native tools."""
    global _BASE_SKILL_REGISTRY
    if _BASE_SKILL_REGISTRY is None:
        import roborsi.embodied.sim.robotwin.robotwin_tools as _t
        _BASE_SKILL_REGISTRY = {
            n[4:]: fn for n, fn in vars(_t).items()
            if n.startswith("_do_") and callable(fn)
        }
    return _BASE_SKILL_REGISTRY


def _execute_arm_plan(state: "DispatchContext", plan: dict, arm: str) -> tuple[dict[str, Any], Observation]:
    """Execute a pre-computed cuRobo joint trajectory directly (no re-planning).
    Bypasses the cuRobo warm-start non-determinism that causes a plan to succeed
    at precheck time but fail when re-queried at execution time.
    plan: the dict returned by left_plan_path / right_plan_path."""
    impl = state.env._impl
    ee_before = impl.robot.get_left_ee_pose() if arm == "left" else impl.robot.get_right_ee_pose()
    control_seq = {
        "left_arm": plan if arm == "left" else None,
        "left_gripper": None,
        "right_arm": plan if arm == "right" else None,
        "right_gripper": None,
    }
    impl.take_dense_action(control_seq, save_freq=None)
    impl.plan_success = True  # take_dense_action doesn't manage plan_success
    ee_after = impl.robot.get_left_ee_pose() if arm == "left" else impl.robot.get_right_ee_pose()
    bx, by, bz = float(ee_before[0]), float(ee_before[1]), float(ee_before[2])
    ax, ay, az = float(ee_after[0]), float(ee_after[1]), float(ee_after[2])
    delta = ((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2) ** 0.5
    moved = delta > 0.002  # lower threshold: pre-verified plan, arm may be close to target
    obs = _snapshot(state.env)
    head = obs.images.get("head_camera")
    if head is not None:
        path = state.workdir / f"after_pose_{len(list(state.workdir.glob('after_pose_*.jpg'))):03d}.jpg"
        _write_jpg(path, head)
        # NO auto-attach (motion tool, pull-on-demand)
    return ({"ok": moved, "arm": arm, "delta_m": round(delta, 4),
             "note": ("Executed stored plan" if moved else
                      "Plan replayed but EE barely moved — arm may already be at target")},
            obs)



def _make_zoom_crop(image_path: Path, u: int, v: int, half_size_px: int,
                    out_dir: Path, tag: str = "zoom") -> tuple[Path | None, dict]:
    """Crop a square around (u, v), 4× upscale, save. Returns (path, window)."""
    import cv2
    img = cv2.imread(str(image_path))
    if img is None:
        return None, {}
    h, w = img.shape[:2]
    if not (0 <= u < w and 0 <= v < h):
        return None, {}
    half = max(20, min(int(half_size_px), 240))
    u0, u1 = max(0, u - half), min(w, u + half)
    v0, v1 = max(0, v - half), min(h, v + half)
    crop = img[v0:v1, u0:u1]
    if crop.size == 0:
        return None, {}
    upscaled = cv2.resize(crop, (crop.shape[1] * 4, crop.shape[0] * 4),
                          interpolation=cv2.INTER_LINEAR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tag}_{len(list(out_dir.glob(tag + '_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(path), upscaled)
    return path, {"u0": u0, "u1": u1, "v0": v0, "v1": v1, "scale": 4}



def _wrap_pi(x: float) -> float:
    return float(((x + np.pi) % (2 * np.pi)) - np.pi)



def _discover_held_actor_name(impl: Any, arm: str) -> str | None:
    """Find the env._impl attribute name of any actor currently in contact
    with the named arm's gripper links. Lets verify_holding_visual run
    the sim-GT path even when callers don't pass actor_name (older skills).
    Returns the attribute name, or None if no contact."""
    gn = impl.robot.left_gripper_name if arm == "left" else impl.robot.right_gripper_name
    joint_names: set[str] = set()
    if isinstance(gn, dict):
        base = gn.get("base")
        if isinstance(base, str):
            joint_names.add(base)
        for entry in (gn.get("mimic") or []):
            if isinstance(entry, (list, tuple)) and entry and isinstance(entry[0], str):
                joint_names.add(entry[0])
    elif isinstance(gn, str):
        joint_names.add(gn)
    gripper_link_names = {jn.replace("joint", "link") for jn in joint_names}
    if not gripper_link_names:
        return None
    touched_scene_names: set[str] = set()
    for c in impl.scene.get_contacts():
        n0, n1 = c.bodies[0].entity.name, c.bodies[1].entity.name
        if n0 in gripper_link_names and n1 not in gripper_link_names:
            touched_scene_names.add(n1)
        elif n1 in gripper_link_names and n0 not in gripper_link_names:
            touched_scene_names.add(n0)
    if not touched_scene_names:
        return None
    for attr_name in vars(impl):
        if attr_name.startswith("_"):
            continue
        obj = getattr(impl, attr_name, None)
        if obj is not None and hasattr(obj, "get_name"):
            if obj.get_name() in touched_scene_names:
                return attr_name
    return None




# ────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ────────────────────────────────────────────────────────────────────────


def _unproject(impl, camera_name: str, u: int, v: int) -> tuple[np.ndarray | None, str]:
    impl._update_render()
    impl.cameras.update_picture()
    config = impl.cameras.get_config().get(camera_name)
    depth = impl.cameras.get_depth().get(camera_name, {}).get("depth")
    if config is None or depth is None:
        return None, f"camera '{camera_name}' missing config/depth"
    h, w = depth.shape
    if not (0 <= u < w and 0 <= v < h):
        return None, f"pixel ({u},{v}) out of range ({w}x{h})"
    z_mm = float(depth[v, u])
    if z_mm <= 1.0:                       # 1 mm = essentially "no return"
        return None, f"depth at ({u},{v}) is invalid ({z_mm:.1f} mm)"
    z = z_mm / 1000.0
    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    x_cam = (u - cx) * z / fx
    y_cam = (v - cy) * z / fy
    p_cam = np.array([x_cam, y_cam, z, 1.0])
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        extr_h = np.eye(4)
        extr_h[:3, :] = extr
        extr = extr_h
    cam2world = np.linalg.inv(extr)
    p_world = cam2world @ p_cam
    return p_world[:3], "ok"



def _execute_move(impl, arm: str, xyz, action: str, height_above: float) -> tuple[bool, str]:
    """Synthesize aloha-style EE pose + Action sequence and dispatch via
    impl.move() so physics ticks. Uses RoboTwin's standard top-down grasp pose.

    Geometry: ``xyz`` is the world-frame point where we want the fingertip
    (top of object, cup rim, button face). RoboTwin ``move_to_pose`` commands
    the EE flange — aloha-agilex has a ~0.15 m flange→fingertip offset for the
    top-down quat below, so we add ``FINGER_OFFSET`` to every commanded z.
    Expert collect_pens.py uses the same +0.18 m offset (slightly larger for
    finger pre-spread).
    """
    from envs.utils.action import Action, ArmTag
    from roborsi.embodied.sim.robotwin.gripper_geom import ALOHA_TCP_IN_EE_LOCAL
    GRASP_QUAT = [0.5, -0.5, 0.5, 0.5]
    # Mesh-measured (URDF + link7.STL). NOT 0.18 (legacy magic) — see
    # gripper_geom.py for derivation.
    FINGER_OFFSET = float(ALOHA_TCP_IN_EE_LOCAL[0])
    HOVER_CLEAR   = 0.10     # fingertip clearance above target before descend
    LIFT_CLEAR    = 0.20     # post-grasp lift clearance
    DROP_CLEAR    = 0.02     # fingertip clearance over target on release
    GRASP_PRESS   = 0.005    # 5 mm below surface so fingertips clamp around
                             # small objects (e.g. 2cm cube on a bowl rim)
                             # rather than just touching the surface and
                             # closing in air. Tradeoff: occasional IK failure
                             # if surface_z is very close to table, but blocks
                             # consistently fail with PRESS=0.

    surface_z = float(xyz[2])
    # height_above_m can be NEGATIVE — meaning "press fingertip below the
    # depth surface by |height_above_m| meters". Useful for thin objects on
    # bigger surfaces (cube on bowl): pass -0.01 to sink fingertip 1cm into
    # the cube body so fingers wrap around the body, not close above it.
    # Clamp to a safe minimum (-3cm) to avoid table-collision failures.
    extra = max(-0.03, float(height_above))
    hover_z = surface_z + FINGER_OFFSET + HOVER_CLEAR + extra
    grasp_z = surface_z + FINGER_OFFSET - GRASP_PRESS              # fingertip slightly below surface
    lift_z  = surface_z + FINGER_OFFSET + LIFT_CLEAR + extra
    drop_z  = surface_z + FINGER_OFFSET + DROP_CLEAR + extra

    hover_pose = [float(xyz[0]), float(xyz[1]), hover_z, *GRASP_QUAT]
    grasp_pose = [float(xyz[0]), float(xyz[1]), grasp_z, *GRASP_QUAT]
    lift_pose  = [float(xyz[0]), float(xyz[1]), lift_z,  *GRASP_QUAT]
    drop_pose  = [float(xyz[0]), float(xyz[1]), drop_z,  *GRASP_QUAT]
    arm_tag = ArmTag(arm)

    impl.plan_success = True

    def _run(seq):
        impl.move((arm_tag, seq))
        return bool(impl.plan_success)

    if action == "hover":
        ok = _run([Action(arm_tag, "move", target_pose=hover_pose)])
        return ok, ("arrived at hover" if ok else "plan to hover failed")
    if action == "grasp":
        ok = _run([
            Action(arm_tag, "open"),
            Action(arm_tag, "move", target_pose=hover_pose),
            Action(arm_tag, "move", target_pose=grasp_pose),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=lift_pose),
        ])
        return ok, ("grasp: hover→descend→close→lift" if ok else "grasp sequence failed")
    if action == "pinch_grasp":
        # Partial-open grasp for tiny objects sitting inside a container.
        # Empirically (002_bowl, 2cm cube on bowl floor): >=0.5 pre-spread
        # frequently snags the bowl rim; 0.45 reliably preserves the bowl
        # but is tight against cube width and often misses laterally. This
        # geometry is adversarial to a top-down 2-finger grasp; consider a
        # different long-horizon strategy (pour from bowl-to-bowl) for cubes
        # nested in deeper containers.
        ok = _run([
            Action(arm_tag, "open", target_gripper_pos=0.45),
            Action(arm_tag, "move", target_pose=hover_pose),
            Action(arm_tag, "move", target_pose=grasp_pose),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=lift_pose),
        ])
        return ok, ("pinch_grasp: 45%open→hover→descend→close→lift" if ok else "pinch_grasp failed")
    if action == "release":
        ok = _run([
            Action(arm_tag, "move", target_pose=drop_pose),
            Action(arm_tag, "open"),
            Action(arm_tag, "move", target_pose=lift_pose),
        ])
        return ok, ("release: hover-over-target→open→retreat" if ok else "release sequence failed")
    if action == "tap":
        ok = _run([
            Action(arm_tag, "move", target_pose=hover_pose),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=grasp_pose),
            Action(arm_tag, "move", target_pose=hover_pose),
        ])
        return ok, ("tap: hover→close→press→retreat" if ok else "tap sequence failed")
    if action == "lateral_grasp":
        # SIDE approach for round/curved objects (bowl rim, ball) where
        # top-down clamping slips. Quat tilted 90° about Y so fingers face
        # horizontally — fingers wrap around the object's curved exterior.
        # Approach from +X side (left arm) or -X side (right arm).
        LATERAL_QUAT = [0.7071, 0.0, 0.0, 0.7071]  # gripper Z points horizontally
        approach_dx = -0.10 if arm == "right" else 0.10  # offset to side
        side_hover = [float(xyz[0]) + approach_dx, float(xyz[1]),
                      float(xyz[2]) + 0.03, *LATERAL_QUAT]
        side_close = [float(xyz[0]),                    float(xyz[1]),
                      float(xyz[2]) + 0.03, *LATERAL_QUAT]
        side_lift  = [float(xyz[0]),                    float(xyz[1]),
                      float(xyz[2]) + LIFT_CLEAR, *LATERAL_QUAT]
        ok = _run([
            Action(arm_tag, "open"),
            Action(arm_tag, "move", target_pose=side_hover),
            Action(arm_tag, "move", target_pose=side_close),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=side_lift),
        ])
        return ok, ("lateral_grasp: side hover→approach→close→lift"
                    if ok else "lateral_grasp sequence failed (planner refused)")
    return False, f"unknown action '{action}'"


def _write_jpg(path: Path, rgb: np.ndarray) -> None:
    import cv2
    arr = rgb[..., ::-1] if rgb.ndim == 3 else rgb
    cv2.imwrite(str(path), arr)
