"""verify_pick_complete — one-call done-gate for a pick (base/libero).

Collapses pick-verification into a single dispatch so the VLM cannot declare done
after a grasp it never confirmed.

PURE PROPRIOCEPTION, NO GROUND TRUTH. The old version read
``<obj>_to_robot0_eef_pos`` and ``<obj>_pos`` (simulator ground-truth object
poses) — a no-GT violation that fed the true object pose to the policy through
the verify gate. It also crashed to ``ok=False`` on a fuzzy name. We now confirm
from the robot's OWN state only: the finger gap (an object wedged between the
jaws holds them open wider than a free close) and, for the optional lift gate,
the end-effector height (proprioception — a proxy for 'raised', not the object's
true z). The caller's object phrase is returned as an unverified label and is
never resolved against simulator object inventory.
"""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def dispatch_runtime(state, args: dict[str, Any]):
    env = state.env
    obs = env.raw_obs()
    ctrl = LiberoControl(env)
    name = str(args.get("object") or "").strip() or None
    # Backward compatibility: min_object_z is treated as min_eef_z.
    min_z = args.get("min_eef_z", args.get("min_object_z"))

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
    return ({"ok": ok, "holding": holding, "lifted": lifted,
             "object": name, "gripper_gap": gap, "gripper_state": grip_state.value,
             "eef_z": eef_z,
             "reason": "; ".join(reasons) if reasons else "grasp confirmed by proprioception (fingers wedged open)",
             "note": ("Proprioceptive gate, no ground-truth pose. ok=True is the "
                      "precondition for done on a pick. If ok=False (small gap = "
                      "closed on air), re-grasp; a THIN object may hold with a small "
                      "gap — cross-check visually.")},
            env.take_snapshot())
