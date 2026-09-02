"""base.robotwin.home — both arms to home pose."""

from __future__ import annotations

from typing import Any


def run(env, **_: Any) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("home requires an active RoboTwinEnv")
    env._impl.robot.move_to_homestate()
    return {"ok": True}
