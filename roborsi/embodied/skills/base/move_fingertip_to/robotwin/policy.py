"""base.robotwin.move_fingertip_to — fingertip-frame motion command.

The standard move_to_pose targets the EE flange. Most grasps require the
FINGERTIP to land on the object — and the flange→fingertip offset is exactly
the kind of detail a VLM shouldn't have to derive from scratch every episode.

This skill takes a fingertip target and dispatches move_to_pose for the
correctly offset flange pose. Uses the runtime-measured TCP offset
(from URDF via gripper_geom.aloha_tcp_in_ee_local), NOT a hardcoded constant.
"""

from __future__ import annotations

import numpy as np

from typing import Any


_DEFAULT_QUAT = [0.5, -0.5, 0.5, 0.5]  # top-down


def _quat_xyzw_to_R(q):
    qx, qy, qz, qw = q
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw),   2*(qx*qz+qy*qw)],
        [2*(qx*qy+qz*qw),   1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
        [2*(qx*qz-qy*qw),   2*(qy*qz+qx*qw),   1-2*(qx*qx+qy*qy)],
    ])


def run(env, arm: str, x: float, y: float, z: float,
        quat: list[float] | None = None, **_: Any) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("move_fingertip_to requires an active RoboTwinEnv")
    if arm not in {"left", "right"}:
        raise ValueError(f"arm must be 'left'|'right', got {arm!r}")
    q = list(quat) if quat is not None else list(_DEFAULT_QUAT)
    if len(q) != 4:
        raise ValueError(f"quat must be length 4, got {len(q)}")

    impl = env._impl
    from roborsi.embodied.sim.robotwin.gripper_geom import flange_from_tcp
    R = _quat_xyzw_to_R(q)
    tcp_world = np.array([float(x), float(y), float(z)])
    flange = flange_from_tcp(impl, tcp_world, R, arm)

    pose = [*flange.tolist(), *q]
    fn = impl.left_move_to_pose if arm == "left" else impl.right_move_to_pose
    impl.plan_success = True
    fn(pose)
    if not impl.plan_success:
        return {"ok": False,
                "reason": f"plan to fingertip=({x:.3f},{y:.3f},{z:.3f}) flange={flange.tolist()} failed",
                "arm": arm, "fingertip_target": [x, y, z], "flange_target": flange.tolist()}
    return {"ok": True, "arm": arm,
            "fingertip_target": [x, y, z],
            "flange_target": flange.tolist(),
            "quat": q}
