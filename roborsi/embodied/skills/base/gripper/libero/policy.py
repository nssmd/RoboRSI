"""gripper — open/close the Panda gripper in place (base/libero)."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def dispatch_runtime(state, args: dict[str, Any]):
    want = str(args.get("state") or "").strip().lower()
    if want not in ("open", "close"):
        return ({"ok": False, "reason": "state must be 'open' or 'close'"},
                state.env.take_snapshot())
    ctrl = LiberoControl(state.env)
    ctrl.set_gripper(close=(want == "close"))
    return ({"ok": True, "is_open": ctrl.is_open()}, state.env.take_snapshot())
