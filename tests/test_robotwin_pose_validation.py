from types import SimpleNamespace

import pytest

from roborsi.embodied.sim.robotwin import robotwin_tools
from roborsi.embodied.agent_loop import rollout


@pytest.mark.parametrize("handler", [robotwin_tools._do_move_to_pose, robotwin_tools._do_move_fingertip_to])
@pytest.mark.parametrize("bad", [
    {"quat": "none"},
    {"quat": {"w": 1, "x": 0, "y": 0, "z": 0}},
    {"quat": [1, 0, 0]},
    {"quat": [1, 0, float("nan"), 0]},
    {"quat": [0, 0, 0, 0]},
    {"x": "not-a-coordinate"},
    {"z": float("inf")},
    {"x": True},
])
def test_invalid_pose_returns_feedback_before_backend_access(monkeypatch, handler, bad):
    observation = object()
    monkeypatch.setattr(rollout, "_snapshot", lambda env: observation)
    # An object with no _impl ensures a malformed pose never reaches motion.
    state = SimpleNamespace(env=object())
    result, obs = handler(state, {"x": 0.1, "y": 0.2, "z": 0.3, **bad})
    assert result["ok"] is False
    assert result["reason"]
    assert obs is observation


def test_default_pose_and_explicit_quaternion_preserve_values():
    xyz, quat = robotwin_tools._pose_components({"x": "0.1", "y": 0.2, "z": 0.3})
    assert xyz == [0.1, 0.2, 0.3]
    assert quat == [0.5, -0.5, 0.5, 0.5]
    _, explicit = robotwin_tools._pose_components({"x": 0, "y": 0, "z": 0, "quat": [1, 0, 0, 0]})
    assert explicit == [1, 0, 0, 0]
