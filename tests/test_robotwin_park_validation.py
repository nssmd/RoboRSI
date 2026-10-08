from types import SimpleNamespace

import pytest

from roborsi.embodied.agent_loop import rollout
from roborsi.embodied.sim.robotwin import robotwin_tools
from roborsi.embodied.skills.base.park_arm.robotwin import policy


@pytest.mark.parametrize("invalid", [
    {"x": ""}, {"y": "none"}, {"z": float("nan")}, {"x": True},
    {"quat": "none"}, {"quat": []}, {"arm": []},
])
def test_invalid_park_arguments_return_feedback_without_motion(monkeypatch, invalid):
    observation = object()
    monkeypatch.setattr(rollout, "_snapshot", lambda env: observation)

    def no_motion(*args, **kwargs):
        pytest.fail("invalid parking input reached motion dispatch")

    monkeypatch.setattr(robotwin_tools, "_do_move_to_pose", no_motion)
    result, obs = policy.dispatch_runtime(SimpleNamespace(env=object()), {"arm": "left", **invalid})
    assert result["ok"] is False and result["reason"]
    assert obs is observation


def test_missing_nullable_coordinates_use_park_defaults(monkeypatch):
    calls = []
    obs = object()

    def motion(state, args):
        calls.append(args)
        return {"ok": True, "ee_after": [args["x"], args["y"], args["z"]]}, obs

    monkeypatch.setattr(robotwin_tools, "_do_move_to_pose", motion)
    result, actual_obs = policy.dispatch_runtime(SimpleNamespace(env=object()), {
        "arm": "right", "x": None, "y": "-0.3", "quat": None,
    })
    assert calls == [{"arm": "right", "x": 0.38, "y": -0.3, "z": 1.05,
                      "quat": [0.5, -0.5, 0.5, 0.5]}]
    assert result["ok"] is True and actual_obs is obs
