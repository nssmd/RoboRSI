"""Confirm a pick from gripper state and optional end-effector height."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def dispatch_runtime(state, args: dict[str, Any]):
    env = state.env
    obs = env.raw_obs()
    ctrl = LiberoControl(env)
    name = str(args.get("object") or "").strip() or None
    min_z = args.get("min_eef_z")

    gap, grip_state = ctrl.read_gripper_state()
    gap = round(float(gap), 4)
    eef = obs.get("robot0_eef_pos")
    eef_z = round(float(eef[2]), 4) if eef is not None else None

    holding = grip_state is GripperState.HELD
    reasons: list[str] = []
    if not holding:
        reasons.append(
            "not holding: gripper state is "
            f"{grip_state.value} (requires held)"
        )

    # Optional lift gate — proprioceptive: the END-EFFECTOR height (not the object's
    # true z, which would be GT). A firm grip + a raised gripper is the best no-GT
    # proxy for 'lifted'.
    lifted = True
    if min_z is not None and eef_z is not None:
        lifted = eef_z >= float(min_z)
        if not lifted:
            reasons.append(f"not lifted: gripper z={eef_z} < min_eef_z={min_z}")

    ok = bool(holding and lifted)
    return (
        {
            "ok": ok,
            "holding": holding,
            "lifted": lifted,
            "object": name,
            "gripper_gap": gap,
            "gripper_state": grip_state.value,
            "eef_z": eef_z,
            "reason": "; ".join(reasons) if reasons else "grasp confirmed",
        },
        env.take_snapshot(),
    )
