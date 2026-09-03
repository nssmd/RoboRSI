from __future__ import annotations

import importlib
from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)


def _policy():
    return importlib.import_module(
        "roborsi.embodied.skills.base.push_object.libero.policy"
    )


class _Env:
    def __init__(self) -> None:
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)

    def take_snapshot(self):
        return SimpleNamespace(images={"head_camera": self.frame.copy()})

    def pixel_to_world(self, u, v, camera="agentview"):
        _ = camera
        return np.array([float(u) * 0.001, float(v) * 0.001, 1.0])

    def raw_obs(self):
        raise AssertionError("push_object must not read hidden simulator state")


class _Control:
    def __init__(self, env) -> None:
        self.env = env
        self.pose = np.array([-0.2, 0.0, 1.2], dtype=float)
        self.state = GripperState.OPEN
        self.calls = []

    def read_pose(self):
        return self.pose.copy(), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

    def read_gripper_state(self):
        return (0.08, self.state) if self.state is GripperState.OPEN else (0.001, self.state)

    def set_gripper(self, *, close, steps=12):
        self.state = GripperState.CLOSED_EMPTY if close else GripperState.OPEN
        self.calls.append(("gripper", bool(close), int(steps)))

    def servo_to(self, pos, **kwargs):
        self.pose = np.asarray(pos, dtype=float)
        self.calls.append(("servo", self.pose.copy(), kwargs))
        return True, None


class _UnderreachingControl(_Control):
    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        servo_count = sum(call[0] == "servo" for call in self.calls)
        if servo_count == 2:
            target = self.pose.copy()
            target[0] += 0.04
        self.pose = target
        self.calls.append(("servo", self.pose.copy(), kwargs))
        return True, None


class _OrientationFallbackControl(_Control):
    def __init__(self, env) -> None:
        super().__init__(env)
        self.failed_initial_hover = False
        self.recovery_calls = 0

    def recover_ready_posture(self, *, max_iters):
        self.recovery_calls += 1
        assert max_iters == 180
        return True, None

    def servo_to(self, pos, **kwargs):
        if not self.failed_initial_hover:
            self.failed_initial_hover = True
            target = np.asarray(pos, dtype=float)
            self.calls.append(("servo", target, kwargs))
            return False, None
        return super().servo_to(pos, **kwargs)


def test_push_object_moves_from_source_toward_target_without_grasp(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
            "max_distance": 0.20,
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert result["pushed"] is True
    assert len(servos) == 4
    hover, contact, push, retract = [call[1] for call in servos]
    assert hover[2] > contact[2]
    assert push[0] > contact[0]
    assert retract[2] > push[2]
    assert servos[1][2]["via_trajopt"] is True
    assert "via_trajopt" not in servos[2][2]
    close_index = next(
        index
        for index, call in enumerate(control.calls)
        if call[0] == "gripper" and call[1] is True
    )
    first_servo_index = next(
        index for index, call in enumerate(control.calls) if call[0] == "servo"
    )
    assert close_index < first_servo_index
    assert result["measured_push_distance"] >= 0.05


def test_push_object_contacts_visual_back_edge(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    source = np.array([0.02, 0.05, 1.0], dtype=float)
    cloud = np.column_stack(
        [
            source[0] + np.linspace(-0.05, 0.05, 41),
            np.full(41, source[1]),
            np.full(41, source[2]),
        ]
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
            "max_distance": 0.20,
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    hover = servos[0][1]
    contact = servos[1][1]
    push_target = servos[2][1]
    assert result["ok"] is True
    assert contact[0] <= source[0] - 0.045
    assert source[0] - hover[0] <= 0.09 + 1e-9
    assert push_target[0] - contact[0] == pytest.approx(0.06)


def test_push_object_retries_hover_with_canonical_topdown_orientation(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    control = _OrientationFallbackControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: None)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
            "max_distance": 0.20,
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert control.recovery_calls == 1
    assert servos[0][2]["quat"] == pytest.approx([0.0, 0.0, 0.0, 1.0])
    assert not np.allclose(servos[1][2]["quat"], servos[0][2]["quat"])
    assert result["orientation_mode"] == "canonical_topdown_fallback"


def test_push_object_rejects_materially_underreached_push(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _UnderreachingControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
            "max_distance": 0.20,
        },
    )

    assert result["requested_push_distance"] == 0.06
    assert result["measured_push_distance"] == 0.04
    assert result["ok"] is False
    assert result["pushed"] is False


def test_push_object_rejects_coincident_pixels(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [20, 50],
        },
    )

    assert result["ok"] is False
    assert "separated" in result["reason"]
    assert control.calls == []


def test_push_object_requires_current_source_semantics(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: False)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
        },
    )

    assert result["ok"] is False
    assert "semantic" in result["reason"]
    assert control.calls == []


def test_push_object_refuses_while_holding(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(env)
    control.state = GripperState.HELD
    monkeypatch.setattr(policy, "LiberoControl", lambda ignored: control)
    monkeypatch.setattr(policy, "_source_semantic_current", lambda *args: True)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "plate",
            "source_pixel": [20, 50],
            "target_pixel": [80, 50],
        },
    )

    assert result["ok"] is False
    assert "holding" in result["reason"]
    assert control.calls == []
