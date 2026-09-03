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

from roborsi.embodied.skills.base._lib.libero._helpers import (
    classify_gripper_gap,
)


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.raw_obs()
    gq = obs.get("robot0_gripper_qpos")
    gap = round(float(gq[0] - gq[1]), 4) if gq is not None else None
    gripper_state = classify_gripper_gap(gap)
    holding = gripper_state == "holding"
    query = str(args.get("object") or "").strip()
    return ({"ok": True, "holding": holding, "gripper_gap": gap,
             "gripper_state": gripper_state, "object": query,
             "note": "proprioceptive finger-gap classification: closed-empty, "
                     "holding, or fully open."},
            state.env.take_snapshot())
