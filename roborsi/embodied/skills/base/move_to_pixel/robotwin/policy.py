"""base.robotwin.move_to_pixel — pixel→world unproject → move + gripper."""

from __future__ import annotations

from typing import Any

import numpy as np


def run(
    env,
    arm: str,
    u: int,
    v: int,
    action: str = "hover",
    height_above_m: float = 0.05,
    camera: str = "head_camera",
    **_: Any,
) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("move_to_pixel requires an active RoboTwinEnv")
    if arm not in {"left", "right"}:
        raise ValueError(f"arm must be 'left'|'right', got {arm!r}")
    impl = env._impl
    xyz, info = _unproject(impl, camera, int(u), int(v))
    if xyz is None:
        return {"ok": False, "reason": info, "ee_xyz": None}
    return _execute(impl, arm, xyz, action, float(height_above_m))


def _unproject(impl, camera_name: str, u: int, v: int):
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
    if z_mm <= 1.0:
        return None, f"depth at ({u},{v}) invalid ({z_mm:.1f} mm)"
    z = z_mm / 1000.0
    K = np.asarray(config["intrinsic_cv"], dtype=np.float64)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    p_cam = np.array([(u - cx) * z / fx, (v - cy) * z / fy, z, 1.0])
    extr = np.asarray(config["extrinsic_cv"], dtype=np.float64)
    if extr.shape == (3, 4):
        h44 = np.eye(4)
        h44[:3, :] = extr
        extr = h44
    p_world = np.linalg.inv(extr) @ p_cam
    return p_world[:3], "ok"


def _execute(impl, arm: str, xyz, action: str, height_above: float) -> dict[str, Any]:
    """Synthesize Action sequence + dispatch via impl.move() so physics ticks.

    Matches RoboTwin's aloha-agilex grasp pose: quat=[0.5,-0.5,0.5,0.5]
    (gripper tilted ~45° as the aloha hand expects). EE Z is biased so the
    fingertips reach the surface point we unprojected.
    """
    from envs.utils.action import Action, ArmTag
    GRASP_QUAT = [0.5, -0.5, 0.5, 0.5]              # aloha-agilex standard
    PRE_GRASP_DIS = 0.10
    GRASP_DIS = 0.10
    DESCEND_DIS = 0.045
    surface_z = float(xyz[2])
    high_z = surface_z + PRE_GRASP_DIS + GRASP_DIS + max(height_above, 0.0)
    descend_z = high_z - DESCEND_DIS
    high_pose = [float(xyz[0]), float(xyz[1]), high_z, *GRASP_QUAT]
    descend_pose = [float(xyz[0]), float(xyz[1]), descend_z, *GRASP_QUAT]
    arm_tag = ArmTag(arm)

    impl.plan_success = True

    def _run(seq):
        impl.move((arm_tag, seq))
        return bool(impl.plan_success)

    if action == "hover":
        ok = _run([Action(arm_tag, "move", target_pose=high_pose)])
        return {"ok": ok, "reason": "arrived at hover" if ok else "plan to hover failed", "ee_xyz": xyz.tolist()}

    if action == "grasp":
        # Pick up: high → close-on-target → descend → lift  (aloha pinch grasp)
        ok = _run([
            Action(arm_tag, "open"),
            Action(arm_tag, "move", target_pose=high_pose),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=descend_pose),
            Action(arm_tag, "move", target_pose=high_pose),
        ])
        return {"ok": ok, "reason": "grasp + lift" if ok else "grasp sequence failed", "ee_xyz": xyz.tolist()}

    if action == "release":
        ok = _run([
            Action(arm_tag, "move", target_pose=descend_pose),
            Action(arm_tag, "open"),
            Action(arm_tag, "move", target_pose=high_pose),
        ])
        return {"ok": ok, "reason": "release + retreat" if ok else "release sequence failed", "ee_xyz": xyz.tolist()}

    if action == "tap":
        # Click / press semantic — aloha grasp_actor + 4.5cm descent.
        ok = _run([
            Action(arm_tag, "move", target_pose=high_pose),
            Action(arm_tag, "close"),
            Action(arm_tag, "move", target_pose=descend_pose),
        ])
        return {"ok": ok, "reason": "tap (close + descend)" if ok else "tap sequence failed", "ee_xyz": xyz.tolist()}

    return {"ok": False, "reason": f"unknown action '{action}'", "ee_xyz": xyz.tolist()}
