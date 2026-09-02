"""base.robotwin.check_dual_arm_collision — exposes dual_arm_collision as
a first-class tool so any skill / Engineer can query collision state.

Three modes:
  - current: use current both-arm qpos
  - candidate_qpos: pass arm + qpos vector
  - candidate_pose: pass arm + flange xyzquat → plan_path → end qpos
"""
from __future__ import annotations

from typing import Any

_TOP_DOWN_QUAT = [0.5, -0.5, 0.5, 0.5]


def _opposite(arm: str) -> str:
    return "right" if arm == "left" else "left"


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.sim.robotwin.dual_arm_collision import (
        check_pair_collision, held_object_spheres,
    )

    mode = str(args.get("mode", "current")).lower()
    arm = str(args.get("arm", "left")).lower()
    container_arm = str(args.get("container_arm") or _opposite(arm)).lower()
    if arm not in {"left", "right"} or container_arm not in {"left", "right"}:
        return ({"ok": False, "reason": "arm and container_arm must be left/right"},
                _snapshot(state.env))
    if arm == container_arm:
        return ({"ok": False, "reason": "arm and container_arm must differ"},
                _snapshot(state.env))

    threshold = float(args.get("clearance_threshold", -0.005))
    attach_left_kind = str(args.get("attached_left", "none")).lower()
    attach_right_kind = str(args.get("attached_right", "none")).lower()
    attach_left = (held_object_spheres(attach_left_kind)
                    if attach_left_kind in ("bowl", "block") else None)
    attach_right = (held_object_spheres(attach_right_kind)
                     if attach_right_kind in ("bowl", "block") else None)
    holding_attach = attach_left if arm == "left" else attach_right
    container_attach = attach_left if container_arm == "left" else attach_right

    impl = state.env._impl

    if mode == "current":
        # Use the arm's CURRENT qpos as the "candidate" — i.e. just check
        # whatever pose the system is in right now.
        entity = (impl.robot.left_entity if arm == "left"
                  else impl.robot.right_entity)
        candidate_qpos = entity.get_qpos()
    elif mode == "candidate_qpos":
        qpos_in = args.get("candidate_qpos")
        if qpos_in is None:
            return ({"ok": False, "reason": "candidate_qpos required in candidate_qpos mode"},
                    _snapshot(state.env))
        candidate_qpos = qpos_in
    elif mode == "candidate_pose":
        x, y, z = args.get("x"), args.get("y"), args.get("z")
        quat = args.get("quat") or _TOP_DOWN_QUAT
        if x is None or y is None or z is None:
            return ({"ok": False, "reason": "x,y,z required in candidate_pose mode"},
                    _snapshot(state.env))
        x, y, z = float(x), float(y), float(z)
        if isinstance(quat, str):
            import ast
            quat = ast.literal_eval(quat)
        quat = [float(q) for q in quat]
        plan_fn = (impl.robot.left_plan_path if arm == "left"
                    else impl.robot.right_plan_path)
        flange = [float(x), float(y), float(z), *quat]
        plan_res = plan_fn(flange)
        if plan_res.get("status") != "Success":
            return ({"ok": False,
                     "reason": f"plan_path failed for {arm} at "
                                f"({x:.3f},{y:.3f},{z:.3f}): {plan_res.get('status')}",
                     "plan_status": plan_res.get("status")},
                    _snapshot(state.env))
        positions = plan_res.get("position")
        if positions is None or len(positions) == 0:
            return ({"ok": False, "reason": "plan_path returned empty position"},
                    _snapshot(state.env))
        candidate_qpos = positions[-1]
    else:
        return ({"ok": False, "reason": f"unknown mode {mode!r}"},
                _snapshot(state.env))

    res = check_pair_collision(
        impl,
        holding_arm=arm,
        container_arm=container_arm,
        candidate_qpos=candidate_qpos,
        holding_attach=holding_attach,
        container_attach=container_attach,
        clearance_threshold=threshold,
    )
    res["mode"] = mode
    res["arm"] = arm
    res["container_arm"] = container_arm
    res["clearance_threshold"] = threshold
    res["attached_left"] = attach_left_kind
    res["attached_right"] = attach_right_kind
    return (res, _snapshot(state.env))
