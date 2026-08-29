"""is_holding — grasp-state check from the robot's OWN proprioception (base/libero).

PURE VISION / PROPRIOCEPTION, NO GROUND TRUTH. The old version read
``<obj>_to_robot0_eef_pos`` (simulator ground-truth object pose) — a no-GT
violation, and it also crashed to ``ok=False`` when the VLM passed a fuzzy name
(``"akita black bowl"``) that didn't match the sim key (``akita_black_bowl_1``).

We now judge holding from the finger gap only (``robot0_gripper_qpos`` is the
robot's own joint state, not object GT): an object wedged between the jaws holds
the gap OPEN wider than a free close, while closing on air collapses it toward 0.
The caller's object phrase is returned only as a label; it is not resolved
against simulator object inventory.

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
    gap, grip_state = ctrl.read_gripper_state()
    holding = grip_state is GripperState.HELD
    name = str(args.get("object") or "").strip() or None
    return ({"ok": True, "holding": holding, "gripper_gap": round(gap, 4), "object": name,
             "gripper_state": grip_state.value,
             "note": "Shared calibrated gripper-state check (no object ground truth)."},
            state.env.take_snapshot())
