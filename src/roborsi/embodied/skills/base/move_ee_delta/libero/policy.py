"""move_ee_delta — relative end-effector nudge (base/libero)."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def dispatch_runtime(state, args: dict[str, Any]):
    dpos = args.get("dpos")
    if not isinstance(dpos, (list, tuple)) or len(dpos) != 3:
        return ({"ok": False, "reason": "dpos must be [dx, dy, dz]"},
                state.env.take_snapshot())
    gripper = str(args.get("gripper") or "keep").strip().lower()
    ctrl = LiberoControl(state.env)
    cur, _, _ = ctrl.read_pose()
    target = (np.asarray(cur, dtype=float) + np.asarray(dpos, dtype=float)).tolist()
    reached, _ = ctrl.servo_to(target, gripper=gripper, max_iters=40)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": bool(reached), "reached": bool(reached),
             "reason": None if reached else "target pose was not reached",
             "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
