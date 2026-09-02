"""base.robotwin.move_to_pose — plan + execute one EE pose."""

from __future__ import annotations

from typing import Any


def run(env, arm: str, pose, **_: Any) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("move_to_pose requires an active RoboTwinEnv")
    if arm not in {"left", "right"}:
        raise ValueError(f"arm must be 'left'|'right', got {arm!r}")
    pose = list(pose)
    if len(pose) == 3:
        pose = [*pose, 0.0, 1.0, 0.0, 0.0]
    if len(pose) != 7:
        raise ValueError(f"pose must be length 3 or 7, got {len(pose)}")
    impl = env._impl
    fn = impl.left_move_to_pose if arm == "left" else impl.right_move_to_pose
    impl.plan_success = True
    fn(pose)
    if not impl.plan_success:
        return {"ok": False, "reason": f"plan to {pose} failed", "arm": arm}
    return {"ok": True, "reason": "moved", "arm": arm, "pose": pose}
