from __future__ import annotations

import importlib
from types import SimpleNamespace

import numpy as np
import pytest

from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)


def _policy():
    return importlib.import_module(
        "roborsi.embodied.skills.base.close_drawer.libero.policy"
    )


class _Env:
    def take_snapshot(self):
        return SimpleNamespace(
            images={"head_camera": np.zeros((100, 100, 3), dtype=np.uint8)}
        )

    def raw_obs(self):
        raise AssertionError("close_drawer must not read hidden simulator state")


class _Control:
    def __init__(self, env) -> None:
        self.env = env
        self.pose = np.array([-0.20, 0.0, 1.10], dtype=float)
        self.calls = []
        self.gripper_state = GripperState.OPEN

    def read_pose(self):
        return self.pose.copy(), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

    def read_gripper_state(self):
        gap = 0.08 if self.gripper_state is GripperState.OPEN else 0.001
        return gap, self.gripper_state

    def set_gripper(self, *, close, steps=12):
        self.gripper_state = (
            GripperState.CLOSED_EMPTY if close else GripperState.OPEN
        )
        self.calls.append(("gripper", bool(close), int(steps)))

    def servo_to(self, pos, **kwargs):
        self.pose = np.asarray(pos, dtype=float)
        self.calls.append(("servo", self.pose.copy(), kwargs))
        return True, None


class _OrientationLimitedControl(_Control):
    def servo_to(self, pos, **kwargs):
        self.pose = np.asarray(pos, dtype=float)
        self.calls.append(("servo", self.pose.copy(), kwargs))
        requested = np.asarray(kwargs.get("quat"), dtype=float)
        return bool(np.allclose(requested, [0.0, 0.0, 0.0, 1.0])), None


class _UnderclosingControl(_Control):
    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        servo_count = sum(call[0] == "servo" for call in self.calls)
        if servo_count == 2:
            target = self.pose.copy()
            target[0] += 0.10
        self.pose = target
        self.calls.append(("servo", self.pose.copy(), kwargs))
        return True, None


class _RetractBlockedControl(_Control):
    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        servo_count = sum(call[0] == "servo" for call in self.calls)
        if servo_count < 3:
            self.pose = target
            reached = True
        else:
            reached = False
        self.calls.append(("servo", self.pose.copy(), kwargs))
        return reached, None


def test_close_drawer_pushes_opposite_visual_outward_normal(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.00]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )
    monkeypatch.setattr(policy, "_visual_drawer_close", lambda *args: 0.16)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "bottom drawer handle",
            "pixel": [50, 50],
            "push_distance": 0.18,
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert result["closed"] is True
    assert len(servos) == 4
    approach, contact, push, retract = [call[1] for call in servos]
    outward = np.array([-1.0, 0.0, 0.0])
    assert float(np.dot(approach - contact, outward)) > 0.05
    assert float(np.dot(push - contact, outward)) < -0.15
    assert float(np.dot(retract - push, outward)) > 0.05
    assert result["measured_push_distance"] == pytest.approx(0.18)


def test_close_drawer_preserves_reachable_wrist_orientation(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _OrientationLimitedControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.00]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )
    monkeypatch.setattr(policy, "_visual_drawer_close", lambda *args: 0.16)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "top drawer handle",
            "pixel": [50, 50],
            "push_distance": 0.18,
        },
    )

    assert result["ok"] is True
    servos = [call for call in control.calls if call[0] == "servo"]
    assert len(servos) == 4
    for _, _, kwargs in servos:
        assert kwargs["quat"] == pytest.approx([0.0, 0.0, 0.0, 1.0])


def test_close_drawer_rejects_partial_ten_centimeter_close(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _UnderclosingControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.00]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )
    monkeypatch.setattr(policy, "_visual_drawer_close", lambda *args: 0.10)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "top drawer handle",
            "pixel": [50, 50],
            "push_distance": 0.12,
        },
    )

    assert result["requested_push_distance"] == 0.18
    assert result["measured_push_distance"] == 0.10
    assert result["ok"] is False
    assert result["closed"] is False


def test_close_drawer_reports_measured_closure_when_retraction_fails(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    control = _RetractBlockedControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.00]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )
    monkeypatch.setattr(policy, "_visual_drawer_close", lambda *args: None)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "top drawer handle",
            "pixel": [50, 50],
            "push_distance": 0.18,
        },
    )

    assert result["measured_push_distance"] == pytest.approx(0.18)
    assert result["retracted"] is False
    assert result["closed"] is True
    assert result["ok"] is True


def test_close_drawer_refuses_while_holding(monkeypatch) -> None:
    policy = _policy()
    control = _Control(_Env())
    control.gripper_state = GripperState.HELD
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=control.env),
        {"object": "top drawer handle", "pixel": [50, 50]},
    )

    assert result["ok"] is False
    assert "holding" in result["reason"]
    assert control.calls == []


def test_close_drawer_rejects_non_drawer_target(monkeypatch) -> None:
    policy = _policy()
    control = _Control(_Env())
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=control.env),
        {"object": "microwave door handle", "pixel": [50, 50]},
    )

    assert result["ok"] is False
    assert "drawer" in result["reason"]
    assert control.calls == []


def test_visual_close_rejects_transverse_handle_jump(monkeypatch) -> None:
    policy = _policy()
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        "roborsi.embodied.skills.base._lib.libero._perception.localize_precise",
        lambda *args, **kwargs: (55, 50),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (55, 50),
            np.array([0.05, 0.20, 1.10]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )

    distance = policy._visual_drawer_close(
        state,
        "bottom drawer handle",
        np.array([0.20, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    )

    assert distance is None
