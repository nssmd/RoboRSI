from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.place_beside.libero.policy as policy
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


class _Env:
    def take_snapshot(self):
        return SimpleNamespace(images={})


class _Control:
    def __init__(
        self,
        *,
        servo_results,
        gripper_states,
        pose_offsets=None,
    ) -> None:
        self.servo_results = list(servo_results)
        self.gripper_states = list(gripper_states)
        self.pose_offsets = list(pose_offsets or [])
        self.calls = []
        self.pose = np.array([0.0, 0.0, 1.0], dtype=float)

    def read_gripper_state(self):
        state = self.gripper_states.pop(0)
        gap = 0.08 if state is GripperState.OPEN else 0.02
        return gap, state

    def read_pose(self):
        return (
            self.pose.copy(),
            np.array([0.0, 0.0, 0.0, 1.0]),
            np.array([0.01, -0.01]),
        )

    def servo_to(self, pos, **kwargs):
        index = len([call for call in self.calls if call[0] == "servo"])
        reached = self.servo_results.pop(0)
        target = np.asarray(pos, dtype=float)
        if reached:
            offset = (
                np.asarray(self.pose_offsets[index], dtype=float)
                if index < len(self.pose_offsets)
                else np.zeros(3)
            )
            self.pose = target + offset
        self.calls.append(("servo", target, kwargs, reached))
        return reached, None

    def set_gripper(self, *, close, steps=12):
        self.calls.append(("gripper", close, steps))


def _run(monkeypatch, control: _Control):
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_reference_xyz",
        lambda *args: np.array([0.20, 0.30, 0.80]),
    )
    return policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"target": "plate", "side": "right"},
    )[0]


def _opened(control: _Control) -> bool:
    return any(call[0] == "gripper" and call[1] is False for call in control.calls)


def test_visual_reference_uses_object_bottom_as_support_height(
    monkeypatch,
) -> None:
    cloud = np.column_stack(
        [
            np.full(101, 0.20),
            np.full(101, 0.30),
            np.linspace(0.90, 1.00, 101),
        ]
    )
    monkeypatch.setattr(perception, "localize_precise", lambda *args: (10, 12))
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    point = policy._reference_xyz(
        SimpleNamespace(env=_Env()),
        "plate",
        None,
    )

    assert point[:2] == pytest.approx([0.20, 0.30])
    assert point[2] == pytest.approx(0.905, abs=1e-6)


@pytest.mark.parametrize(
    ("side", "expected_y"),
    [("left", 0.18), ("right", 0.42)],
)
def test_language_left_right_follow_head_camera_horizontal_axis(
    monkeypatch,
    side,
    expected_y,
) -> None:
    control = _Control(
        servo_results=[True, True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_reference_xyz",
        lambda *args: np.array([0.20, 0.30, 0.80]),
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "target": "plate",
            "side": side,
            "gap": 0.12,
        },
    )

    assert result["released"] is True
    approach = next(call[1] for call in control.calls if call[0] == "servo")
    assert approach[1] == pytest.approx(expected_y)


def test_place_beside_approach_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[False],
        gripper_states=[GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert _opened(control) is False


def test_place_beside_approach_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True],
        gripper_states=[GripperState.HELD],
        pose_offsets=[[0.0, 0.0, 0.04]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["approach_z_error"] == 0.04
    assert _opened(control) is False


def test_place_beside_final_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, False],
        gripper_states=[GripperState.HELD, GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_place_beside_final_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.03, 0.0, 0.02]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["final_position_error"] > 0.02
    assert result["final_z_error"] == 0.02
    assert _opened(control) is False


def test_place_beside_just_over_final_z_tolerance_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.00804]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert _opened(control) is False


def test_place_beside_lost_hold_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.CLOSED_EMPTY,
        ],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["gripper_state_pre_release"] == "closed_empty"
    assert _opened(control) is False


def test_place_beside_verifies_open_before_reporting_release(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
    )

    result = _run(monkeypatch, control)

    open_calls = [
        call for call in control.calls if call[0] == "gripper" and call[1] is False
    ]
    assert len(open_calls) == 2
    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False


def test_place_beside_success_requires_reached_hold_and_open(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["reached"] is True
    assert result["released"] is True
    assert result["gripper_opened"] is True
    assert _opened(control) is True
