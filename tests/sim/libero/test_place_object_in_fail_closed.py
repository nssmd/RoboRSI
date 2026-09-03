from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.place_object_in.libero.policy as policy
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


@pytest.fixture(autouse=True)
def _valid_visual_hold(monkeypatch) -> None:
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            reason="ok",
            object_name="held object",
            identity_verified=True,
        ),
    )
    monkeypatch.setattr(
        policy,
        "_verify_post_release_containment",
        lambda *args, **kwargs: {
            "verified": True,
            "placed": True,
            "reason": "test containment confirmed",
        },
        raising=False,
    )


class _Env:
    def take_snapshot(self):
        return SimpleNamespace(
            images={
                "head_camera": np.zeros((32, 32, 3), dtype=np.uint8)
            }
        )

    def pixel_to_world(self, u, v, camera="agentview"):
        return np.array([0.20, 0.30, 0.80], dtype=float)


def _container_cloud() -> np.ndarray:
    xs = np.linspace(0.16, 0.24, 7)
    ys = np.linspace(0.27, 0.33, 7)
    rows = []
    for z in np.linspace(0.78, 0.82, 4):
        rows.extend((x, 0.27, z) for x in xs)
        rows.extend((x, 0.33, z) for x in xs)
        rows.extend((0.16, y, z) for y in ys)
        rows.extend((0.24, y, z) for y in ys)
    rows.extend((x, y, 0.78) for x in xs[1:-1] for y in ys[1:-1])
    return np.asarray(rows, dtype=float)


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
        self.last_gripper_state = (
            self.gripper_states[-1]
            if self.gripper_states
            else GripperState.AMBIGUOUS
        )
        self.pose_offsets = list(pose_offsets or [])
        self.calls = []
        self.pose = np.array([0.0, 0.0, 1.0], dtype=float)

    def read_gripper_state(self):
        if self.gripper_states:
            self.last_gripper_state = self.gripper_states.pop(0)
        gap = 0.08 if self.last_gripper_state is GripperState.OPEN else 0.02
        return gap, self.last_gripper_state

    def read_pose(self):
        return self.pose.copy(), np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

    def servo_to(self, pos, **kwargs):
        index = len([call for call in self.calls if call[0] == "servo"])
        reached = self.servo_results.pop(0) if self.servo_results else False
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


def _run(monkeypatch, control: _Control):
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: _container_cloud(),
    )
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            reason="ok",
            object_name="held object",
            identity_verified=True,
        ),
    )
    return policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12], "hover": 0.12},
    )[0]


def _opened(control: _Control) -> bool:
    return any(call[0] == "gripper" and call[1] is False for call in control.calls)


def test_place_object_in_requires_initial_hold(monkeypatch) -> None:
    control = _Control(
        servo_results=[],
        gripper_states=[GripperState.CLOSED_EMPTY],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert control.calls == []


def test_side_entry_release_hint_lowers_requested_clearance() -> None:
    evidence = SimpleNamespace(release_clearance_hint=0.04)

    clearance, applied = policy._release_clearance(0.06, evidence)

    assert clearance == 0.04
    assert applied is True


def test_low_side_grip_retries_open_after_small_lift(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True, True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.AMBIGUOUS,
            GripperState.AMBIGUOUS,
            GripperState.HELD,
            GripperState.OPEN,
        ],
    )
    monkeypatch.setattr(
        policy,
        "get_visual_hold",
        lambda _env: SimpleNamespace(
            object_name="held object",
            identity_verified=True,
            object_offset_local=None,
            release_clearance_hint=0.04,
        ),
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["released"] is True
    assert result["release_open_recovery_attempted"] is True
    assert result["release_open_recovery_regrasped"] is True
    assert result["release_open_recovery_lifted"] is True
    assert result["release_clearance"] == 0.06


def test_place_object_in_requires_visual_hold_at_entry(monkeypatch) -> None:
    control = _Control(
        servo_results=[],
        gripper_states=[GripperState.HELD],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            reason="missing_visual_hold_evidence",
        ),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12]},
    )[0]

    assert result["released"] is False
    assert result["reason"] == "visual hold evidence is invalid"
    assert control.calls == []


def test_place_object_in_requires_verified_held_identity(monkeypatch) -> None:
    control = _Control(
        servo_results=[],
        gripper_states=[GripperState.HELD],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            reason="source_patch_remains_cleared",
            object_name="alphabet soup can",
            identity_verified=False,
        ),
    )
    monkeypatch.setattr(
        policy,
        "get_visual_hold",
        lambda env: SimpleNamespace(
            object_name="alphabet soup can",
            identity_verified=False,
            object_offset_local=None,
        ),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12]},
    )[0]

    assert result["ok"] is False
    assert result["released"] is False
    assert result["placed"] is False
    assert result["reason"] == "held object identity is not verified"
    assert control.calls == []


def test_place_object_in_approach_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[False, False],
        gripper_states=[GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_place_object_in_approach_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True],
        gripper_states=[GripperState.HELD],
        pose_offsets=[[0.0, 0.0, 0.08]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["approach_z_error"] == 0.08
    assert _opened(control) is False


def test_place_object_in_accepts_nearby_high_approach_measurement(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
        pose_offsets=[[0.0, 0.0, 0.045]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["released"] is True
    assert result["placed"] is True


def test_place_object_in_lost_hold_after_approach_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True],
        gripper_states=[GripperState.HELD, GripperState.CLOSED_EMPTY],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["hold_check_stage"] == "approach"
    assert _opened(control) is False


def test_place_object_in_final_failure_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, False],
        gripper_states=[GripperState.HELD, GripperState.HELD],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_place_object_in_releases_from_bounded_container_clearance(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ROBORSI_CONTAINER_ADAPTIVE_RELEASE",
        "1",
    )
    control = _Control(
        servo_results=[True, True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.04],
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(policy, "get_visual_hold", lambda env: object())

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "basket",
            "pixel": [10, 12],
            "hover": 0.10,
        },
    )[0]

    assert result["ok"] is True
    assert result["released"] is True
    assert result["placed"] is True
    assert result["adaptive_clearance_release"] is True
    assert _opened(control) is True


def test_place_object_in_does_not_release_from_excessive_clearance(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, False],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
        ],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_place_object_in_adaptive_release_requires_visual_hold(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ROBORSI_CONTAINER_ADAPTIVE_RELEASE",
        "1",
    )
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.04],
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(policy, "get_visual_hold", lambda env: None)

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "basket",
            "pixel": [10, 12],
            "hover": 0.10,
        },
    )[0]

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_place_object_in_adaptive_release_is_disabled_by_default(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.04],
        ],
    )
    monkeypatch.delenv(
        "ROBORSI_CONTAINER_ADAPTIVE_RELEASE",
        raising=False,
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(policy, "get_visual_hold", lambda env: object())

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "basket",
            "pixel": [10, 12],
            "hover": 0.10,
        },
    )[0]

    assert result["ok"] is False
    assert result["released"] is False
    assert _opened(control) is False


def test_generic_container_uses_depth_fallback_without_claiming_containment(
    monkeypatch,
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
        perception,
        "object_cloud",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(policy, "get_visual_hold", lambda env: None)
    monkeypatch.setattr(
        policy,
        "_verify_post_release_containment",
        lambda *args, **kwargs: pytest.fail(
            "depth fallback must not claim visual containment"
        ),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "basket",
            "pixel": [10, 12],
            "hover": 0.10,
        },
    )[0]

    assert result["ok"] is True
    assert result["motion_ok"] is True
    assert result["released"] is True
    assert result["placed"] is None
    assert result["action_grounded_release"] is True
    assert result["pixel_fallback"] == "depth_neighborhood"
    assert result["post_release_visual_containment"]["verified"] is False
    assert _opened(control) is True


def test_place_object_in_final_measurement_error_never_opens(monkeypatch) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.07, 0.0, 0.03]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["final_position_error"] > 0.06
    assert result["final_z_error"] == 0.03
    assert _opened(control) is False


def test_place_object_in_just_over_final_z_tolerance_never_opens(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.02004]],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert _opened(control) is False


def test_place_object_in_lost_hold_before_release_never_opens(monkeypatch) -> None:
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


def test_place_object_in_rechecks_visual_hold_before_release(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
    )
    checks = iter(
        [
            SimpleNamespace(
                ok=True,
                reason="ok",
                object_name="held object",
                identity_verified=True,
            ),
            SimpleNamespace(ok=False, reason="source_patch_reoccupied"),
        ]
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: next(checks),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12]},
    )[0]

    assert result["released"] is False
    assert result["reason"] == "visual hold evidence failed before release"
    assert _opened(control) is False


def test_place_object_in_verifies_open_before_reporting_release(monkeypatch) -> None:
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


def test_place_object_in_success_requires_reached_hold_and_open(monkeypatch) -> None:
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
    assert result["placed"] is True
    assert result["gripper_opened"] is True


def test_place_object_in_retraction_failure_reports_release_honestly(
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
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is True
    assert result["reached"] is True
    assert result["released"] is True
    assert result["placed"] is True
    assert result["gripper_opened"] is True


def test_place_object_in_corrects_bounded_final_residual_once(
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
                    [-0.03, 0.0, 0.05],
                    dtype=float,
                )
                reached = False
            elif index == 2:
                assert self.nominal_final is not None
                assert np.linalg.norm(target - self.pose) <= 0.060001
                self.pose = self.nominal_final.copy()
                reached = False
            else:
                self.pose = target.copy()
                reached = True
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

    control = _CorrectionControl()
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            object_name="held object",
            identity_verified=True,
        ),
        raising=False,
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12], "hover": 0.12},
    )[0]

    assert result["ok"] is True
    assert result["released"] is True
    assert result["placed"] is True
    assert result["final_correction_attempted"] is True
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 4


def test_place_object_in_does_not_correct_excessive_final_residual(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [-0.04, 0.0, 0.056],
        ],
    )

    result = _run(monkeypatch, control)

    assert result["ok"] is False
    assert result["released"] is False
    assert result["final_correction_attempted"] is False
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 2
    assert _opened(control) is False


def test_place_object_in_correction_requires_visual_hold(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [-0.03, 0.0, 0.05],
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    checks = iter(
        [
            SimpleNamespace(
                ok=True,
                reason="ok",
                object_name="held object",
                identity_verified=True,
            ),
            SimpleNamespace(ok=False, reason="source_patch_reoccupied"),
        ]
    )
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: next(checks),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12], "hover": 0.12},
    )[0]

    assert result["released"] is False
    assert result["final_correction_attempted"] is False
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 2
    assert _opened(control) is False


def test_place_object_in_correction_requires_continuous_hold(
    monkeypatch,
) -> None:
    control = _Control(
        servo_results=[True, True],
        gripper_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.CLOSED_EMPTY,
        ],
        pose_offsets=[
            [0.0, 0.0, 0.0],
            [-0.03, 0.0, 0.05],
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            object_name="held object",
            identity_verified=True,
        ),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12], "hover": 0.12},
    )[0]

    assert result["released"] is False
    assert result["final_correction_attempted"] is False
    assert _opened(control) is False


def test_place_object_in_correction_must_pass_original_gate(
    monkeypatch,
) -> None:
    class _StillHighControl(_Control):
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
            if index == 0:
                self.pose = target.copy()
                reached = True
            else:
                self.nominal_final = target.copy()
                self.pose = target + np.array(
                    [-0.03, 0.0, 0.05],
                    dtype=float,
                )
                reached = False
            self.calls.append(("servo", target, kwargs, reached))
            return reached, None

        def servo_correction_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            self.pose = self.nominal_final + np.array(
                [0.0, 0.0, 0.0201],
                dtype=float,
            )
            self.calls.append(("servo", target, kwargs, False))
            return False, None

    control = _StillHighControl()
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: _container_cloud())
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            object_name="held object",
            identity_verified=True,
        ),
    )

    result = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "basket", "pixel": [10, 12], "hover": 0.12},
    )[0]

    assert result["released"] is False
    assert result["final_correction_attempted"] is True
    assert result["final_z_error"] == 0.0201
    assert _opened(control) is False
