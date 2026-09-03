from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.place_held_at_target_servo.libero.policy as policy
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


class _Env:
    def take_snapshot(self):
        return SimpleNamespace(images={})

    def raw_obs(self):
        raise AssertionError("placement must not read simulator object inventory")


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
        self.quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=float)

    def read_gripper_state(self):
        state = self.gripper_states.pop(0)
        gap = 0.08 if state is GripperState.OPEN else 0.02
        return gap, state

    def read_pose(self):
        return self.pose.copy(), self.quat.copy(), np.array([0.01, -0.01])

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

    def servo_correction_to(self, pos, **kwargs):
        return self.servo_to(pos, **kwargs)

    def set_gripper(self, *, close, steps=12):
        self.calls.append(("gripper", close, steps))


def _run(monkeypatch, control: _Control, args=None):
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    return policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        args or {"pos": [0.20, 0.30, 0.80], "pos_tol": 0.01},
    )[0]


def _opened(control: _Control) -> bool:
    return any(call[0] == "gripper" and call[1] is False for call in control.calls)


def test_exact_place_approach_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[False],
        gripper_states=[GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert _opened(control) is False


def test_exact_place_approach_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True],
        gripper_states=[GripperState.HELD],
        pose_offsets=[[0.0, 0.0, 0.04]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["approach_z_error"] == 0.04
    assert _opened(control) is False


def test_exact_place_final_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, False],
        gripper_states=[GripperState.HELD, GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_exact_place_final_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True, False],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.02, 0.0, 0.015]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["final_position_error"] > 0.01
    assert result["final_z_error"] == 0.015
    assert _opened(control) is False


def test_exact_place_just_over_final_position_tolerance_never_opens(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True, False],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.01004, 0.0, 0.0]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert _opened(control) is False


def test_exact_place_lost_hold_never_opens(monkeypatch) -> None:
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


def test_exact_place_verifies_open_before_reporting_release(monkeypatch) -> None:
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

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert len(
        [call for call in control.calls if call[0] == "gripper" and call[1] is False]
    ) == 2


def test_exact_place_success_requires_reached_hold_and_open(monkeypatch) -> None:
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


def test_exact_place_localization_failure_does_not_expose_inventory(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True],
        gripper_states=[GripperState.HELD],
    )
    monkeypatch.setattr(perception, "_place_fix_on", lambda: False)
    monkeypatch.setattr(perception, "localize_precise", lambda *args: None)

    result = _run(monkeypatch, control, {"object": "white plate"})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert "available" not in result


def test_exact_place_corrects_near_approach_residual_once(
    monkeypatch,
) -> None:
    class _CorrectionControl(_Control):
        def __init__(self):
            super().__init__(
                servo_results=[],
                gripper_states=[
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.OPEN,
                ],
            )
            self.nominal_approach = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            index = len(
                [call for call in self.calls if call[0] == "servo"]
            )
            if index == 0:
                self.nominal_approach = target.copy()
                self.pose = target + np.array(
                    [0.0, 0.0, 0.019],
                    dtype=float,
                )
                reached = False
            elif index == 1:
                assert np.linalg.norm(target - self.pose) <= 0.030001
                self.pose = self.nominal_approach.copy()
                reached = False
            else:
                self.pose = target.copy()
                reached = True
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

    control = _CorrectionControl()

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["released"] is True
    assert result["correction_stage"] == "approach"
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 4


def test_exact_place_corrects_near_final_residual_once(
    monkeypatch,
) -> None:
    class _CorrectionControl(_Control):
        def __init__(self):
            super().__init__(
                servo_results=[],
                gripper_states=[
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.OPEN,
                ],
            )
            self.nominal_final = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            index = len(
                [call for call in self.calls if call[0] == "servo"]
            )
            if index == 0:
                self.pose = target.copy()
                reached = True
            elif index == 1:
                self.nominal_final = target.copy()
                self.pose = target + np.array(
                    [0.0, 0.0, 0.02],
                    dtype=float,
                )
                reached = False
            elif index == 2:
                assert np.linalg.norm(target - self.pose) <= 0.030001
                self.pose = self.nominal_final.copy()
                reached = False
            else:
                self.pose = target.copy()
                reached = True
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

    control = _CorrectionControl()

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["released"] is True
    assert result["correction_stage"] == "final"


def test_exact_place_never_uses_second_correction(
    monkeypatch,
) -> None:
    class _SingleTokenControl(_Control):
        def __init__(self):
            super().__init__(
                servo_results=[],
                gripper_states=[
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                ],
            )
            self.nominal_approach = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            index = len(
                [call for call in self.calls if call[0] == "servo"]
            )
            if index == 0:
                self.nominal_approach = target.copy()
                self.pose = target + np.array(
                    [0.0, 0.0, 0.019],
                    dtype=float,
                )
                reached = False
            elif index == 1:
                self.pose = self.nominal_approach.copy()
                reached = False
            else:
                self.pose = target + np.array(
                    [0.0, 0.0, 0.02],
                    dtype=float,
                )
                reached = False
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

    control = _SingleTokenControl()

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert result["correction_stage"] == "approach"
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 3
    assert _opened(control) is False


def test_exact_place_correction_preserves_rotation_gate(
    monkeypatch,
) -> None:
    class _RotationControl(_Control):
        def __init__(self):
            super().__init__(
                servo_results=[],
                gripper_states=[
                    GripperState.HELD,
                    GripperState.HELD,
                    GripperState.HELD,
                ],
            )
            self.nominal_final = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            index = len(
                [call for call in self.calls if call[0] == "servo"]
            )
            desired_quat = np.asarray(kwargs.get("quat"), dtype=float)
            if index == 0:
                self.pose = target.copy()
                self.quat = desired_quat.copy()
                reached = True
            else:
                self.nominal_final = target.copy()
                self.pose = target + np.array(
                    [0.0, 0.0, 0.02],
                    dtype=float,
                )
                self.quat = desired_quat.copy()
                reached = False
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

        def servo_correction_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            self.pose = self.nominal_final.copy()
            self.quat = np.array(
                [0.0, 0.0, np.sin(0.06), np.cos(0.06)],
                dtype=float,
            )
            self.calls.append(("servo", target, kwargs, False))
            return False, None

    control = _RotationControl()

    result = _run(
        monkeypatch,
        control,
        {
            "pos": [0.20, 0.30, 0.80],
            "pos_tol": 0.01,
            "quat": [0.0, 0.0, 0.0, 1.0],
        },
    )

    assert result["released"] is False
    assert result["correction_stage"] == "final"
    assert _opened(control) is False
