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

# Finger gap (qpos[0]-qpos[1]) above this ⇒ an object is wedged between the jaws.
# Measured on LIBERO: held-wide ≈ 0.063, free-open ≈ 0.042, closed-on-air ≈ 0.
_HELD_GAP = 0.05


def dispatch_runtime(state, args: dict[str, Any]):
    obs = state.env.raw_obs()
    gq = obs.get("robot0_gripper_qpos")
    gap = round(float(gq[0] - gq[1]), 4) if gq is not None else None
    holding = bool(gap is not None and gap > _HELD_GAP)
    query = str(args.get("object") or "").strip()
    return ({"ok": True, "holding": holding, "gripper_gap": gap, "object": query,
             "note": "proprioceptive finger-gap check; a thin "
                     "rim grip gives a small gap — reason from gripper_gap too."},
            state.env.take_snapshot())
