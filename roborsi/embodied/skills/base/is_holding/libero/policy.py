"""is_holding — grasp-state check from robot proprioception (base/libero).

Holding is judged from the finger gap only: an object wedged between the jaws
holds the gap wider than a free close, while closing on air collapses it toward
zero.

Caveat (reported honestly to the caller): a THIN object (bowl rim) gives a small
gap indistinguishable from closed-on-air, so ``gripper_gap`` is returned raw for
the caller to reason about; ``holding`` is the confident-wide-grip signal.
"""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def dispatch_runtime(state, args: dict[str, Any]):
    ctrl = LiberoControl(state.env)
    gap, gripper_state = ctrl.read_gripper_state()
    holding = gripper_state is GripperState.HELD
    query = str(args.get("object") or "").strip()
    return ({"ok": True, "holding": holding,
             "gripper_gap": round(float(gap), 4),
             "gripper_state": gripper_state.value, "object": query,
             "note": "Shared calibrated gripper-state check (no object ground truth)."},
            state.env.take_snapshot())
