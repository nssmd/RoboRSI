"""base.robotwin.set_gripper — open/close one or both grippers."""

from __future__ import annotations

from typing import Any


def run(env, state: str, arm: str = "both", **_: Any) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("set_gripper requires an active RoboTwinEnv")
    if state not in {"open", "close"}:
        raise ValueError(f"state must be 'open'|'close', got {state!r}")
    if arm not in {"left", "right", "both"}:
        raise ValueError(f"arm must be 'left'|'right'|'both', got {arm!r}")
    impl = env._impl
    pos_open, pos_close = 1, 0
    target = pos_open if state == "open" else pos_close
    if arm == "both":
        if state == "open":
            impl.together_open_gripper(left_pos=pos_open, right_pos=pos_open)
        else:
            impl.together_close_gripper(left_pos=pos_close, right_pos=pos_close)
    else:
        # together_*_gripper supports single-arm by setting the other pos to "leave alone";
        # RoboTwin's API uses left/right_pos params, both required, so we keep the inactive
        # arm at its current desired state.
        active_left = target if arm == "left" else 1
        active_right = target if arm == "right" else 1
        if state == "open":
            impl.together_open_gripper(left_pos=active_left, right_pos=active_right)
        else:
            impl.together_close_gripper(left_pos=active_left, right_pos=active_right)
    return {"ok": True, "arm": arm, "state": state}
