"""gripper — open/close the Panda gripper in place (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)


def dispatch_runtime(state, args: dict[str, Any]):
    want = str(args.get("state") or "").strip().lower()
    if want not in ("open", "close"):
        return ({"ok": False, "reason": "state must be 'open' or 'close'"},
                state.env.take_snapshot())
    ctrl = LiberoControl(state.env)
    ctrl.set_gripper(close=(want == "close"))
    gap, actual = ctrl.read_gripper_state()
    is_open = actual is GripperState.OPEN
    reached = (
        is_open
        if want == "open"
        else actual in {GripperState.HELD, GripperState.CLOSED_EMPTY}
    )
    return (
        {
            "ok": bool(reached),
            "is_open": is_open,
            "gripper_state": actual.value,
            "gripper_gap": round(float(gap), 4),
            "reason": (
                None
                if reached
                else "gripper did not reach requested state"
            ),
        },
        state.env.take_snapshot(),
    )
