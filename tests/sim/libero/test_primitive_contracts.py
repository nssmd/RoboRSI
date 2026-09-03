from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from roborsi.embodied.skills.base._lib.libero import _control as control_module
from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)
from roborsi.embodied.skills.base.descend_tcp_to_z.libero import (
    policy as descend_policy,
)
from roborsi.embodied.skills.base.gripper.libero import policy as gripper_policy
from roborsi.embodied.skills.base.home.libero import policy as home_policy
from roborsi.embodied.skills.base.move_ee_delta.libero import (
    policy as delta_policy,
)
from roborsi.embodied.skills.base.move_to_pixel.libero import (
    policy as pixel_policy,
)
from roborsi.embodied.skills.base.move_to_pose.libero import (
    policy as pose_policy,
)


class _Env:
    def take_snapshot(self):
        return SimpleNamespace()

    def pixel_to_world(self, u, v, camera):
        return [0.1, 0.2, 0.3]


class _FailedMotionControl:
    def __init__(self, env):
        self.env = env
        self._pose = [0.0, 0.0, 0.5]

    def read_pose(self):
        return self._pose, [0.0, 0.0, 0.0, 1.0], [0.0, 0.0]

    def servo_to(self, *args, **kwargs):
        return False, None


@pytest.mark.parametrize(
    ("module", "args"),
    (
        (pose_policy, {"pos": [0.1, 0.2, 0.3]}),
        (descend_policy, {"target_z": 0.3}),
        (delta_policy, {"dpos": [0.0, 0.0, 0.1]}),
        (pixel_policy, {"u": 10, "v": 12}),
        (home_policy, {"lift": 0.18}),
    ),
)
def test_motion_primitive_reports_failed_reach_as_not_ok(
    monkeypatch,
    module,
    args,
) -> None:
    monkeypatch.setattr(
        module,
        "LiberoControl",
        _FailedMotionControl,
    )

    result = module.dispatch_runtime(SimpleNamespace(env=_Env()), args)[0]

    assert result["ok"] is False
    assert result["reached"] is False
    assert result["reason"] == "target pose was not reached"


class _GripperControl:
    def __init__(self, env):
        self.env = env

    def set_gripper(self, close):
        return None

    def read_gripper_state(self):
        return 0.01, GripperState.HELD


def test_gripper_open_request_reports_failure_when_gripper_stays_closed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        gripper_policy,
        "LiberoControl",
        _GripperControl,
    )

    result = gripper_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"state": "open"},
    )[0]

    assert result["ok"] is False
    assert result["is_open"] is False
    assert result["reason"] == "gripper did not reach requested state"


def test_pyroki_frame_uses_environment_robot_base(monkeypatch) -> None:
    transform_utils = SimpleNamespace(
        quat2mat=lambda quat: np.eye(3, dtype=float),
    )
    monkeypatch.setitem(sys.modules, "robosuite", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "robosuite.utils",
        SimpleNamespace(transform_utils=transform_utils),
    )
    control = object.__new__(LiberoControl)
    control._base_world = [0.0, 0.0, 0.0]
    control.read_pose = lambda: (
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        [0.0, 0.0],
    )
    control._base_world = [-0.75, 0.0, 0.912]

    _, position = control._pose_in_base(
        [-0.10, 0.0, 1.0],
        [0.0, 0.0, 0.0, 1.0],
    )

    assert position.tolist() == pytest.approx([0.65, 0.0, -0.009])


def test_goal_ik_request_carries_live_arm_joints(monkeypatch) -> None:
    captured = {}

    def request(payload):
        captured.update(payload)
        return {
            "protocol": "roborsi.pyroki.live_joints.v1",
            "joints": [0.0] * 7,
        }

    monkeypatch.setattr(control_module, "_request", request)
    current = np.arange(7, dtype=float)

    result = control_module._solve_ik_zmq(
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.0, 0.5]),
        current,
    )

    assert result.tolist() == [0.0] * 7
    assert captured["protocol"] == "roborsi.pyroki.live_joints.v1"
    assert captured["current_joints"] == current.tolist()


def test_goal_ik_rejects_old_unversioned_service_response(monkeypatch) -> None:
    monkeypatch.setattr(
        control_module,
        "_request",
        lambda payload: {"joints": [0.0] * 7},
    )

    result = control_module._solve_ik_zmq(
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.0, 0.5]),
        np.arange(7, dtype=float),
    )

    assert result is None


def test_trajopt_request_pins_exact_start_joints(monkeypatch) -> None:
    captured = {}

    def request(payload):
        captured.update(payload)
        return {
            "protocol": "roborsi.pyroki.live_joints.v1",
            "traj": [
                [float(index) for index in range(7)],
                [float(index) + 0.1 for index in range(7)],
            ],
            "start_error_max": 0.0,
        }

    monkeypatch.setattr(control_module, "_request", request)
    current = np.arange(7, dtype=float)

    result = control_module._trajopt_zmq(
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.0, 0.5]),
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.2, 0.3]),
        current,
        10,
    )

    assert result.shape == (2, 7)
    assert captured["protocol"] == "roborsi.pyroki.live_joints.v1"
    assert captured["start_joints"] == current.tolist()


def test_trajopt_rejects_unversioned_branch_response(monkeypatch) -> None:
    monkeypatch.setattr(
        control_module,
        "_request",
        lambda payload: {
            "traj": [[0.0] * 7, [0.1] * 7],
            "start_error_max": 0.0,
        },
    )

    result = control_module._trajopt_zmq(
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.0, 0.5]),
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.4, 0.2, 0.3]),
        np.zeros(7, dtype=float),
        10,
    )

    assert result is None


def test_previewed_trajectory_rejects_changed_joint_start() -> None:
    control = object.__new__(LiberoControl)
    control._arm_qpos = lambda: np.ones(7)
    control._servo_via_trajopt = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("branch-mismatched preview must not execute")
    )

    reached, last = control.execute_previewed_trajectory(
        np.vstack([np.zeros(7), np.full(7, 0.1)]),
        pos=np.array([0.0, 0.0, 1.0]),
        quat=None,
        gripper="open",
    )

    assert reached is False
    assert last is None


def test_joint_recovery_trajopt_pins_live_start_and_ready_end(monkeypatch) -> None:
    captured = {}

    def request(payload):
        captured.update(payload)
        return {
            "protocol": "roborsi.pyroki.live_joints.v1",
            "traj": [[0.0] * 7, [0.2] * 7],
            "start_error_max": 0.0,
            "end_error_max": 0.0,
        }

    monkeypatch.setattr(control_module, "_request", request)
    result = control_module._joint_trajopt_zmq(
        np.zeros(7),
        np.full(7, 0.2),
        10,
    )

    assert result.shape == (2, 7)
    assert captured["op"] == "joint_trajopt"
    assert captured["start_joints"] == [0.0] * 7
    assert captured["end_joints"] == [0.2] * 7
