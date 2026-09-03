from __future__ import annotations

import math

import pytest


def test_wire_protocol_identifies_live_joint_contract() -> None:
    from scripts.pyroki_protocol import WIRE_PROTOCOL

    assert WIRE_PROTOCOL == "roborsi.pyroki.live_joints.v1"


def test_validate_arm_joints_accepts_exact_finite_seven() -> None:
    from scripts.pyroki_protocol import validate_arm_joints

    assert validate_arm_joints(range(7), field="current_joints") == [
        float(index) for index in range(7)
    ]


@pytest.mark.parametrize(
    "value",
    (
        [0.0] * 6,
        [0.0] * 8,
        [0.0, 0.0, 0.0, math.nan, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, True, 0.0, 0.0, 0.0],
        "not-a-joint-vector",
    ),
)
def test_validate_arm_joints_rejects_malformed_values(value) -> None:
    from scripts.pyroki_protocol import validate_arm_joints

    with pytest.raises(ValueError, match="current_joints"):
        validate_arm_joints(value, field="current_joints")


def test_trajectory_start_error_uses_submitted_live_joints() -> None:
    from scripts.pyroki_protocol import trajectory_start_error

    assert trajectory_start_error([[0.0] * 7, [0.1] * 7], [0.0] * 7) == 0.0
    assert trajectory_start_error([[0.25] * 7], [0.0] * 7) == pytest.approx(0.25)


def test_trajectory_start_error_rejects_empty_or_bad_shape() -> None:
    from scripts.pyroki_protocol import trajectory_start_error

    with pytest.raises(ValueError, match="trajectory"):
        trajectory_start_error([], [0.0] * 7)
    with pytest.raises(ValueError, match="trajectory"):
        trajectory_start_error([[0.0] * 6], [0.0] * 7)
