from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base.is_holding.libero.policy as holding_policy
import roborsi.embodied.skills.base.verify_pick_complete.libero.policy as verify_policy
from roborsi.embodied.skills.base._lib.libero._control import LiberoControl
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperCalibration,
    GripperClassifier,
    GripperState,
)
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    get_pending_visual_hold,
    get_visual_hold,
    record_pending_visual_hold,
    record_visual_hold,
)

CAL = GripperCalibration(
    open_gap=0.08,
    closed_gap=0.0,
    settled_sigma=0.0002,
)


def test_fully_open_is_never_holding() -> None:
    assert CAL.classify(0.0798, last_command="close") == GripperState.OPEN


def test_closed_on_air_is_empty() -> None:
    assert CAL.classify(0.001, last_command="close") == GripperState.CLOSED_EMPTY


def test_mid_gap_after_close_is_held() -> None:
    assert CAL.classify(0.011, last_command="close") == GripperState.HELD
    assert CAL.classify(0.049, last_command="close") == GripperState.HELD
    assert CAL.classify(0.063, last_command="close") == GripperState.HELD


def test_mid_gap_without_close_intent_is_ambiguous() -> None:
    assert CAL.classify(0.049, last_command=None) == GripperState.AMBIGUOUS


def test_repeated_close_hold_stays_held_for_12_keep_steps() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.079, post_gap=0.014)
    assert clf.classify(0.014, last_command="close") == GripperState.HELD
    for _ in range(12):
        clf.on_keep_close(gap=0.014)
    assert clf.classify(0.014, last_command="close") == GripperState.HELD


def test_frozen_open_close_does_not_confirm_held() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.079, post_gap=0.078)
    assert clf.classify(0.078, last_command="close") == GripperState.OPEN


def test_close_without_motion_is_not_held_even_mid_gap() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.030, post_gap=0.0298)
    assert clf.classify(0.0298, last_command="close") != GripperState.HELD


def test_second_explicit_close_without_motion_keeps_existing_hold_latch() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.079, post_gap=0.014)
    assert clf.classify(0.014, last_command="close") == GripperState.HELD
    clf.confirm_close(pre_gap=0.014, post_gap=0.014)
    assert clf.classify(0.014, last_command="close") == GripperState.HELD


def test_close_near_closed_endpoint_is_closed_empty_even_with_large_motion() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.079, post_gap=0.001)
    assert clf.classify(0.001, last_command="close") == GripperState.CLOSED_EMPTY


def test_latched_hold_clears_when_gap_slides_to_closed_endpoint() -> None:
    clf = GripperClassifier(CAL)
    clf.confirm_close(pre_gap=0.079, post_gap=0.014)
    assert clf.classify(0.014, last_command="close") == GripperState.HELD
    assert clf.classify(0.0001, last_command="close") == GripperState.CLOSED_EMPTY
    assert clf.hold_latched is False


def test_settled_sigma_derives_from_joint_span() -> None:
    wide = GripperCalibration.from_joint_ranges((0.0, 0.04), (-0.04, 0.0))
    narrow = GripperCalibration.from_joint_ranges((0.0, 0.01), (-0.01, 0.0))
    assert wide.settled_sigma > narrow.settled_sigma


def test_from_joint_ranges_near_closed_gap_fails_closed_not_held() -> None:
    cal = GripperCalibration.from_joint_ranges((0.0, 0.04), (-0.04, 0.0))
    state = cal.classify(0.001, last_command="close")
    assert state != GripperState.HELD


def test_endpoint_sampler_updates_only_on_open_or_closed_empty() -> None:
    clf = GripperClassifier(CAL)
    before_open = clf.open_samples
    before_closed = clf.closed_samples
    clf.classify(0.011, last_command="close")  # held band
    assert clf.open_samples == before_open
    assert clf.closed_samples == before_closed
    clf.classify(0.0799, last_command="open")
    clf.classify(0.0001, last_command="close")
    assert clf.open_samples > before_open
    assert clf.closed_samples > before_closed


def test_dynamic_tolerance_uses_endpoint_variance() -> None:
    clf = GripperClassifier(CAL)
    base_tol = clf.calibration.tolerance
    for gap in (0.0796, 0.0802, 0.0798, 0.0803, 0.0797):
        clf.classify(gap, last_command="open")
    assert clf.endpoint_tolerance > base_tol


def test_get_arm_pose_uses_shared_gripper_classifier() -> None:
    root = Path(__file__).resolve().parents[3]
    policy = root / "roborsi/embodied/skills/base/get_arm_pose/libero/policy.py"
    text = policy.read_text()
    assert "read_gripper_state" in text
    assert "gq[0] - gq[1]" not in text


def test_gripper_tools_share_control_classifier() -> None:
    root = Path(__file__).resolve().parents[3]
    files = [
        root / "roborsi/embodied/skills/base/grasp_object/libero/policy.py",
        root / "roborsi/embodied/skills/base/is_holding/libero/policy.py",
        root / "roborsi/embodied/skills/base/verify_pick_complete/libero/policy.py",
    ]
    for path in files:
        text = path.read_text()
        assert "read_gripper_state" in text


@pytest.mark.parametrize("module", (holding_policy, verify_policy))
def test_holding_tools_do_not_resolve_simulator_object_inventory(
    monkeypatch,
    module,
) -> None:
    class _Env:
        def raw_obs(self):
            return {
                "robot0_eef_pos": np.array([0.0, 0.0, 1.0]),
                "hidden_object_pos": np.array([0.1, 0.2, 0.3]),
            }

        def take_snapshot(self):
            return SimpleNamespace(images={})

    class _Control:
        def __init__(self, env):
            self.env = env

        def read_gripper_state(self):
            return 0.02, GripperState.HELD

    monkeypatch.setattr(module, "LiberoControl", _Control)

    result, _ = module.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "hidden object"},
    )

    assert result["object"] == "hidden object"


def test_open_gripper_clears_visual_hold_evidence() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    env = SimpleNamespace()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )
    assert get_visual_hold(env) is not None

    class _Classifier:
        def confirm_open(self, *, gap):
            return None

    ctrl = object.__new__(LiberoControl)
    ctrl.env = env
    ctrl._set_last_gripper_command = lambda command: None
    ctrl._gripper_gap = lambda: 0.08
    ctrl._step = lambda dq, grip: SimpleNamespace(done=False)
    ctrl._gripper_classifier = lambda: _Classifier()

    ctrl.set_gripper(close=False, steps=1)

    assert get_visual_hold(env) is None


def test_open_gripper_motion_step_clears_visual_hold_evidence() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200

    class _Env:
        def step(self, action):
            return SimpleNamespace(done=False)

    env = _Env()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )
    assert get_visual_hold(env) is not None

    class _Classifier:
        def on_keep_open(self, *, gap):
            return None

    ctrl = object.__new__(LiberoControl)
    ctrl.env = env
    ctrl._adim = 8
    ctrl._set_last_gripper_command = lambda command: None
    ctrl._gripper_gap = lambda: 0.08
    ctrl._gripper_classifier = lambda: _Classifier()

    ctrl._step(np.zeros(7), -1.0)

    assert get_visual_hold(env) is None


def test_open_gripper_clears_evidence_before_failed_motion_step() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200

    class _Env:
        def step(self, action):
            raise RuntimeError("sim step failed after open command")

    env = _Env()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )

    ctrl = object.__new__(LiberoControl)
    ctrl.env = env
    ctrl._adim = 8

    with pytest.raises(RuntimeError, match="sim step failed"):
        ctrl._step(np.zeros(7), -1.0)

    assert get_visual_hold(env) is None


def test_open_servo_clears_evidence_when_already_at_target() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    env = SimpleNamespace()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )

    ctrl = object.__new__(LiberoControl)
    ctrl.env = env
    ctrl._goal_config = lambda pos, quat: np.zeros(7)
    ctrl._servo_to_config = lambda *args: (True, None, False)

    reached, _ = ctrl.servo_to(
        [0.1, 0.2, 0.3],
        gripper="open",
    )

    assert reached is True
    assert get_visual_hold(env) is None


def test_non_held_gripper_read_clears_pending_evidence() -> None:
    env = SimpleNamespace(
        _libero_last_gripper_command="close",
    )
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=np.zeros((32, 32, 3), dtype=np.uint8),
        before_depth=np.full((32, 32), 0.42, dtype=np.float32),
    )

    class _Classifier:
        def classify(self, gap, last_command=None):
            _ = (gap, last_command)
            return GripperState.CLOSED_EMPTY

    ctrl = object.__new__(LiberoControl)
    ctrl.env = env
    ctrl._gripper_gap = lambda: 0.001
    ctrl._gripper_classifier = lambda: _Classifier()

    _, state = ctrl.read_gripper_state()

    assert state is GripperState.CLOSED_EMPTY
    assert get_pending_visual_hold(env) is None
