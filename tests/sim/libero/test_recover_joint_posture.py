from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from roborsi.embodied.skills.base._lib.libero import _control as control_module
from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)
from roborsi.embodied.skills.base.recover_joint_posture.libero import (
    policy as recover_policy,
)


def test_control_recovers_to_episode_ready_joint_state() -> None:
    current = np.asarray([0.7, -0.6, 0.5, -0.4, 0.3, 0.2, -0.1])
    ready = np.asarray([0.0, -0.2, 0.1, -0.8, 0.0, 0.7, 0.1])
    control = object.__new__(LiberoControl)
    control.env = SimpleNamespace(_libero_ready_joint_qpos=ready.copy())
    control._arm_qpos = lambda: current.copy()
    control._hold_grip = lambda: -1.0

    def _step(dq, grip):
        assert grip == -1.0
        current[:] += np.clip(dq, -0.12, 0.12)
        return SimpleNamespace(done=False)

    control._step = _step

    reached, _ = control.recover_ready_posture(max_iters=80)

    assert reached is True
    assert np.max(np.abs(current - ready)) <= 0.03


def test_large_recovery_uses_collision_aware_joint_path(monkeypatch) -> None:
    current = np.asarray([1.0, -0.8, 0.7, -0.3, 0.5, 0.1, -0.4])
    ready = np.asarray([0.0, -0.2, 0.1, -0.8, 0.0, 0.7, 0.1])
    captured = {}

    def _plan(start, end, timesteps):
        captured["start"] = np.asarray(start).copy()
        captured["end"] = np.asarray(end).copy()
        captured["timesteps"] = timesteps
        return np.linspace(start, end, timesteps)

    monkeypatch.setattr(control_module, "_joint_trajopt_zmq", _plan, raising=False)
    control = object.__new__(LiberoControl)
    control.env = SimpleNamespace(_libero_ready_joint_qpos=ready.copy())
    control._arm_qpos = lambda: current.copy()
    control._hold_grip = lambda: -1.0

    def _step(dq, grip):
        assert grip == -1.0
        current[:] += np.clip(dq, -0.12, 0.12)
        return SimpleNamespace(done=False)

    control._step = _step

    reached, _ = control.recover_ready_posture(max_iters=240)

    assert reached is True
    assert captured["start"] == pytest.approx(
        [1.0, -0.8, 0.7, -0.3, 0.5, 0.1, -0.4]
    )
    assert captured["end"] == pytest.approx(ready)
    assert captured["timesteps"] == 10


def test_recover_joint_posture_refuses_confirmed_hold(monkeypatch) -> None:
    class _Control:
        def __init__(self, env):
            pass

        def read_gripper_state(self):
            return 0.04, GripperState.HELD

        def recover_ready_posture(self, **kwargs):
            raise AssertionError("held object must block posture reset")

        def joint_posture_error(self):
            return 0.5

    monkeypatch.setattr(recover_policy, "LiberoControl", _Control)
    state = SimpleNamespace(env=SimpleNamespace(take_snapshot=lambda: SimpleNamespace()))

    result, _ = recover_policy.dispatch_runtime(state, {})

    assert result["ok"] is False
    assert result["reason"] == "confirmed hold blocks joint-posture recovery"


def test_recover_joint_posture_runs_when_gripper_is_empty(monkeypatch) -> None:
    class _Control:
        def __init__(self, env):
            pass

        def read_gripper_state(self):
            return 0.08, GripperState.OPEN

        def recover_ready_posture(self, **kwargs):
            assert kwargs == {"max_iters": 240}
            return True, None

        def joint_posture_error(self):
            return 0.01

    monkeypatch.setattr(recover_policy, "LiberoControl", _Control)
    state = SimpleNamespace(env=SimpleNamespace(take_snapshot=lambda: SimpleNamespace()))

    result, _ = recover_policy.dispatch_runtime(state, {})

    assert result == {
        "ok": True,
        "reached": True,
        "reason": None,
        "joint_error_max": 0.01,
    }
