from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.place_on_surface.libero.policy as policy
from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
from roborsi.embodied.skills import get_ns
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    clear_visual_hold,
    record_visual_hold,
)
from scripts.check_libero_gt_leak import scan_policy_path, scan_skill_doc_path


class FakeEnv:
    def __init__(self) -> None:
        before = np.zeros((32, 32, 3), dtype=np.uint8)
        current = before.copy()
        current[8:24, 8:24] = 200
        self.snapshot = SimpleNamespace(
            images={"head_camera": current},
            state=np.zeros(1, dtype=np.float32),
        )
        self.depth_point = np.array([0.15, 0.25, 0.80], dtype=float)
        record_visual_hold(
            self,
            object_name="white mug",
            source_pixel=(16, 16),
            before_rgb=before,
            after_rgb=current,
        )

    def take_snapshot(self):
        return self.snapshot

    def pixel_to_world(self, u, v, camera="agentview"):
        return self.depth_point


class FakeTargetControl:
    def __init__(self, env):
        self.env = env

    def read_pose(self):
        return (
            np.array([0.0, 0.0, 1.0], dtype=float),
            np.array([0.0, 0.0, 0.0, 1.0], dtype=float),
            np.array([0.02, -0.02], dtype=float),
        )

    def servo_to(self, *args, **kwargs):
        return True, None


class FakeControl:
    def __init__(
        self,
        env,
        *,
        held: bool = True,
        approach_reached: bool = True,
        stage_reached: bool = True,
        stage_xy_offset: tuple[float, float] = (0.0, 0.0),
        stage_z_offset: float = 0.0,
        descent_reached: bool = True,
        update_descent_pose: bool = True,
        final_z_offset: float = 0.0,
        invalid_final_pose: bool = False,
        reoccupy_after_stage: bool = False,
        retract_reached: bool = True,
        open_states=None,
        hold_states=None,
    ):
        self.env = env
        self.pose = np.array([0.0, 0.0, 1.0], dtype=float)
        self.quat = np.array([0.1, 0.2, 0.3, 0.9], dtype=float)
        self.calls = []
        self.held = held
        self.approach_reached = approach_reached
        self.stage_reached = stage_reached
        self.stage_xy_offset = stage_xy_offset
        self.stage_z_offset = stage_z_offset
        self.descent_reached = descent_reached
        self.update_descent_pose = update_descent_pose
        self.final_z_offset = final_z_offset
        self.invalid_final_pose = invalid_final_pose
        self.reoccupy_after_stage = reoccupy_after_stage
        self.retract_reached = retract_reached
        self.open_states = list(open_states or [GripperState.OPEN])
        self.hold_states = list(
            hold_states or [GripperState.HELD, GripperState.HELD]
        )
        self.hold_reads = 0
        self.open_calls = 0

    def read_pose(self):
        return self.pose.copy(), self.quat.copy(), np.array([0.02, -0.02])

    def read_gripper_state(self):
        if self.open_calls:
            state = self.open_states[
                min(self.open_calls - 1, len(self.open_states) - 1)
            ]
            gap = 0.08 if state is GripperState.OPEN else 0.01
            return gap, state
        if not self.held:
            return 0.001, GripperState.CLOSED_EMPTY
        state = self.hold_states[
            min(self.hold_reads, len(self.hold_states) - 1)
        ]
        self.hold_reads += 1
        gap = 0.02 if state is GripperState.HELD else 0.001
        return gap, state

    def servo_to(self, pos, **kwargs):
        if kwargs.get("via_trajopt"):
            reached = self.approach_reached
        elif kwargs.get("gripper") == "open":
            reached = self.retract_reached
        elif kwargs.get("max_iters") == 150:
            reached = self.descent_reached
        else:
            reached = self.stage_reached
        self.calls.append(("servo", np.asarray(pos, dtype=float), kwargs, reached))
        is_final_descent = kwargs.get("max_iters") == 150
        if reached and (not is_final_descent or self.update_descent_pose):
            self.pose = np.asarray(pos, dtype=float).copy()
            if (
                kwargs.get("max_iters") == 80
                and kwargs.get("gripper") == "close"
            ):
                self.pose[:2] += np.asarray(
                    self.stage_xy_offset,
                    dtype=float,
                )
                self.pose[2] += self.stage_z_offset
            if is_final_descent:
                self.pose[2] += self.final_z_offset
                if self.invalid_final_pose:
                    self.pose[0] = np.nan
        if (
            reached
            and kwargs.get("max_iters") == 80
            and kwargs.get("gripper") == "close"
            and self.reoccupy_after_stage
        ):
            self.env.snapshot.images["head_camera"] = np.zeros(
                (32, 32, 3),
                dtype=np.uint8,
            )
        return reached, None

    def servo_correction_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        self.calls.append(("correction", target, kwargs, True))
        self.pose = target.copy()
        return True, None

    def set_gripper(self, *, close, steps=12):
        self.calls.append(("gripper", close))
        if not close:
            self.open_calls += 1


def _patch_target(monkeypatch):
    target = policy.SurfaceTarget(
        pixel=(10, 12),
        world=np.array([0.15, 0.25, 0.80], dtype=float),
        source="sam_cloud",
    )
    monkeypatch.setattr(
        policy,
        "_resolve_surface_target",
        lambda *args, **kwargs: target,
    )
    return target


def test_place_on_surface_is_visible_with_expected_schema() -> None:
    skill = get_ns("place_on_surface", "libero")
    assert skill is not None

    args = (skill.frontmatter or {})["args"]
    assert set(args) == {
        "target",
        "pixel",
        "release_clearance",
        "hover",
        "pos_tol",
    }
    description = str((skill.frontmatter or {})["description"]).lower()
    assert "plate" in description
    assert "container" in description


def test_explicit_pixel_uses_sam_surface(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    cloud = np.array(
        [[0.14, 0.24, 0.79], [0.16, 0.26, 0.81]] * 12,
        dtype=float,
    )
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[10, 12],
    )

    assert target is not None
    assert target.pixel == (10, 12)
    assert target.source == "sam_cloud"
    assert target.world[:2] == pytest.approx([0.15, 0.25])
    assert target.world[2] == pytest.approx(float(np.percentile(cloud[:, 2], 85)))


def test_same_surface_pixel_reuses_cached_world_target(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    clear_cloud = np.array(
        [[0.14, 0.24, 0.79], [0.16, 0.26, 0.81]] * 12,
        dtype=float,
    )
    occluded_cloud = np.array(
        [[0.18, 0.27, 0.94], [0.19, 0.28, 0.97]] * 12,
        dtype=float,
    )
    calls = {"count": 0}

    def object_cloud(*args, **kwargs):
        calls["count"] += 1
        return clear_cloud if calls["count"] == 1 else occluded_cloud

    monkeypatch.setattr(perception, "object_cloud", object_cloud)

    first = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="the plate",
        pixel=[10, 12],
    )
    second = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="plate",
        pixel=[10, 12],
    )

    assert first is not None
    assert second is not None
    assert calls["count"] == 1
    assert second.world == pytest.approx(first.world)


def test_sparse_cloud_falls_back_to_depth(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: None)

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[10, 12],
    )

    assert target is not None
    assert target.source == "depth_unproject"
    assert target.world == pytest.approx([0.15, 0.25, 0.80])


def test_inconsistent_cloud_uses_depth(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    cloud = np.array(
        [[0.50, 0.55, 0.82], [0.51, 0.54, 0.84]] * 12,
        dtype=float,
    )
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[10, 12],
    )

    assert target is not None
    assert target.source == "depth_unproject_inconsistent_cloud"
    assert target.world == pytest.approx([0.15, 0.25, 0.80])


def test_out_of_frame_pixel_is_rejected(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: pytest.fail("must not query an invalid pixel"),
    )

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[32, 12],
    )

    assert target is None


def test_nonfinite_cloud_rows_fall_back_to_depth(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    cloud = np.full((24, 3), np.nan, dtype=float)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[10, 12],
    )

    assert target is not None
    assert target.source == "depth_unproject"
    assert target.world == pytest.approx([0.15, 0.25, 0.80])


def test_invalid_cloud_and_depth_are_rejected(monkeypatch) -> None:
    env = FakeEnv()
    env.depth_point = np.array([np.nan, 0.25, 0.80], dtype=float)
    state = SimpleNamespace(env=env)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: None)

    target = policy._resolve_surface_target(
        state,
        FakeTargetControl(env),
        target_name="",
        pixel=[10, 12],
    )

    assert target is None


def test_named_target_reuses_visual_resolver(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeTargetControl(env)
    called = {"retreat": 0}
    held_quat = np.array([0.1, 0.2, 0.3, 0.9], dtype=float)
    cloud = np.array(
        [[0.14, 0.24, 0.79], [0.16, 0.26, 0.81]] * 12,
        dtype=float,
    )
    monkeypatch.setattr(
        perception,
        "retreat_from_head_view",
        lambda env, ctrl, quat=None: (
            called.__setitem__("retreat", called["retreat"] + 1)
            or called.__setitem__("quat", np.asarray(quat, dtype=float))
            or True
        ),
    )
    monkeypatch.setattr(perception, "localize_precise", lambda state, name: (10, 12))
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: cloud)

    target = policy._resolve_surface_target(
        state,
        control,
        target_name="white plate",
        pixel=None,
        held_quat=held_quat,
    )

    assert called["retreat"] == 1
    assert called["quat"] == pytest.approx(held_quat)
    assert target is not None
    assert target.pixel == (10, 12)


def test_named_target_retreat_failure_skips_localization(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeTargetControl(env)
    localized = {"calls": 0}
    monkeypatch.setattr(
        perception,
        "retreat_from_head_view",
        lambda env, ctrl, quat=None: False,
    )
    monkeypatch.setattr(
        perception,
        "localize_precise",
        lambda state, name: localized.__setitem__(
            "calls",
            localized["calls"] + 1,
        ),
    )

    target = policy._resolve_surface_target(
        state,
        control,
        target_name="white plate",
        pixel=None,
        held_quat=np.array([0.1, 0.2, 0.3, 0.9], dtype=float),
    )

    assert target is None
    assert localized["calls"] == 0


def test_retreat_from_head_view_forwards_quaternion_and_reach() -> None:
    held_quat = np.array([0.1, 0.2, 0.3, 0.9], dtype=float)

    class _Env:
        def robot_base_pos(self):
            return np.array([-0.66, 0.0, 0.912], dtype=float)

    class _Control:
        def __init__(self):
            self.kwargs = None

        def read_pose(self):
            return (
                np.array([0.0, 0.2, 0.9], dtype=float),
                held_quat.copy(),
                np.zeros(2),
            )

        def servo_to(self, pos, **kwargs):
            self.kwargs = kwargs
            return False, None

    control = _Control()
    reached = perception.retreat_from_head_view(
        _Env(),
        control,
        quat=held_quat,
    )

    assert reached is False
    assert control.kwargs["quat"] == pytest.approx(held_quat)


def test_retreat_from_head_view_falls_back_to_lift_then_back() -> None:
    held_quat = np.array([0.1, 0.2, 0.3, 0.9], dtype=float)

    class _Env:
        def robot_base_pos(self):
            return np.array([-0.66, 0.0, 0.912], dtype=float)

    class _Control:
        def __init__(self):
            self.pose = np.array([0.0, 0.2, 0.9], dtype=float)
            self.results = [False, True, True]
            self.calls = []

        def read_pose(self):
            return self.pose.copy(), held_quat.copy(), np.zeros(2)

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            reached = self.results.pop(0)
            if reached:
                self.pose = target
            self.calls.append((target, kwargs, reached))
            return reached, None

    control = _Control()

    reached = perception.retreat_from_head_view(
        _Env(),
        control,
        quat=held_quat,
    )

    assert reached is True
    assert len(control.calls) == 3
    assert control.calls[1][0][:2] == pytest.approx([0.0, 0.2])
    assert control.calls[2][0][0] < control.calls[1][0][0]
    assert all(
        call[1]["quat"] == pytest.approx(held_quat)
        for call in control.calls
    )


def test_retreat_from_head_view_never_descends_above_soft_ceiling() -> None:
    class _Env:
        def robot_base_pos(self):
            return np.array([-0.66, 0.0, 0.912], dtype=float)

    class _Control:
        def __init__(self):
            self.pose = np.array([0.0, 0.2, 1.20], dtype=float)
            self.calls = []

        def read_pose(self):
            return (
                self.pose.copy(),
                np.array([0.0, 0.0, 0.0, 1.0], dtype=float),
                np.zeros(2),
            )

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            self.calls.append((target, kwargs))
            self.pose = target
            return True, None

    control = _Control()

    reached = perception.retreat_from_head_view(
        _Env(),
        control,
        lift=0.04,
        back=0.10,
        clear_z=0.42,
        z_ceiling=1.15,
    )

    assert reached is True
    assert control.calls
    assert all(call[0][2] >= 1.20 for call in control.calls)


def test_retreat_fallback_never_descends_above_soft_ceiling() -> None:
    class _Env:
        def robot_base_pos(self):
            return np.array([-0.66, 0.0, 0.912], dtype=float)

    class _Control:
        def __init__(self):
            self.pose = np.array([0.0, 0.2, 1.20], dtype=float)
            self.results = [False, True, True]
            self.calls = []

        def read_pose(self):
            return (
                self.pose.copy(),
                np.array([0.0, 0.0, 0.0, 1.0], dtype=float),
                np.zeros(2),
            )

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            reached = self.results.pop(0)
            self.calls.append((target, kwargs, reached))
            if reached:
                self.pose = target
            return reached, None

    control = _Control()

    reached = perception.retreat_from_head_view(
        _Env(),
        control,
        lift=0.04,
        back=0.10,
        clear_z=0.42,
        z_ceiling=1.15,
    )

    assert reached is True
    assert len(control.calls) == 3
    assert all(call[0][2] >= 1.20 for call in control.calls)


def test_not_holding_refuses_without_motion(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, held=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)

    result, _ = policy.dispatch_runtime(state, {"target": "white plate"})

    assert result["ok"] is False
    assert result["released"] is False
    assert control.calls == []


def test_failed_approach_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, approach_reached=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["approach_target"] == pytest.approx([0.15, 0.25, 0.945])
    assert result["approach_position_error"] is not None
    assert result["approach_z_error"] is not None
    assert result["approach_rotation_error_rad"] == pytest.approx(0.0)
    assert result["ee_pos"] == pytest.approx([0.0, 0.0, 1.0])
    assert not any(call[0] == "gripper" for call in control.calls)


def test_near_hover_residual_gets_one_bounded_xy_correction(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)

    class _NearResidualControl(FakeControl):
        def __init__(self, env):
            super().__init__(env, approach_reached=False)
            self.nominal_hover = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            if kwargs.get("via_trajopt"):
                self.nominal_hover = target.copy()
                self.pose = target + np.array(
                    [-0.0255, 0.0, 0.002],
                    dtype=float,
                )
                self.calls.append(("servo", target, kwargs, False))
                return False, None
            if len(self.calls) == 1:
                assert self.nominal_hover is not None
                residual = self.nominal_hover - self.pose
                correction_move = target - self.pose
                assert np.linalg.norm(correction_move) <= 0.030001
                assert float(
                    np.dot(correction_move[:2], residual[:2])
                ) > 0.0
                assert target[2] == pytest.approx(self.nominal_hover[2])
                self.pose = self.nominal_hover.copy()
                self.calls.append(("servo", target, kwargs, False))
                return False, None
            return super().servo_to(pos, **kwargs)

    control = _NearResidualControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.03},
    )

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert len(servo_calls) == 5
    assert result["ok"] is True
    assert result["released"] is True
    assert result["approach_correction_attempted"] is True


@pytest.mark.parametrize(
    ("position_offset", "quat"),
    (
        (
            np.array([-0.031, 0.0, 0.0], dtype=float),
            np.array([0.1, 0.2, 0.3, 0.9], dtype=float),
        ),
        (
            np.array([-0.025, 0.0, 0.016], dtype=float),
            np.array([0.1, 0.2, 0.3, 0.9], dtype=float),
        ),
        (
            np.array([-0.025, 0.0, 0.0], dtype=float),
            np.array([0.0, 0.0, 0.05, 0.998749], dtype=float),
        ),
    ),
)
def test_approach_correction_rejects_out_of_bounds_pose(
    position_offset,
    quat,
) -> None:
    target = np.array([0.15, 0.25, 0.945], dtype=float)
    target_quat = np.array([0.1, 0.2, 0.3, 0.9], dtype=float)

    assert (
        policy._approach_correction_target(
            target,
            target + position_offset,
            quat,
            target_quat,
            gate_pos_tol=0.015,
        )
        is None
    )


def test_hover_correction_is_blocked_when_hold_is_lost(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)

    class _LostHoldControl(FakeControl):
        def __init__(self, env):
            super().__init__(
                env,
                approach_reached=False,
                hold_states=[
                    GripperState.HELD,
                    GripperState.CLOSED_EMPTY,
                ],
            )

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            if kwargs.get("via_trajopt"):
                self.pose = target + np.array(
                    [-0.0255, 0.0, 0.002],
                    dtype=float,
                )
                self.calls.append(("servo", target, kwargs, False))
                return False, None
            return super().servo_to(pos, **kwargs)

    control = _LostHoldControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.03},
    )

    assert result["released"] is False
    assert result["approach_correction_attempted"] is False
    assert result["approach_correction_blocked_reason"] == (
        "hold_lost_before_correction"
    )
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 1


def test_hover_correction_must_reach_nominal_gate(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)

    class _StillShortControl(FakeControl):
        def __init__(self, env):
            super().__init__(env, approach_reached=False)
            self.nominal_hover = None

        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            if kwargs.get("via_trajopt"):
                self.nominal_hover = target.copy()
                self.pose = target + np.array(
                    [-0.0255, 0.0, 0.002],
                    dtype=float,
                )
                self.calls.append(("servo", target, kwargs, False))
                return False, None
            if len(self.calls) == 1:
                self.pose = self.nominal_hover + np.array(
                    [-0.016, 0.0, 0.0],
                    dtype=float,
                )
                self.calls.append(("servo", target, kwargs, False))
                return False, None
            return super().servo_to(pos, **kwargs)

    control = _StillShortControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.03},
    )

    assert result["released"] is False
    assert result["approach_correction_attempted"] is True
    assert result["approach_position_error"] == pytest.approx(0.016)
    assert len(
        [call for call in control.calls if call[0] == "servo"]
    ) == 2
    assert not any(call[0] == "gripper" for call in control.calls)


def test_hold_lost_after_approach_stops_before_stage(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(
        env,
        hold_states=[GripperState.HELD, GripperState.CLOSED_EMPTY],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is False
    assert result["released"] is False
    assert result["hold_check_stage"] == "approach"
    assert len(servo_calls) == 1


def test_failed_descent_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, descent_reached=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert not any(call[0] == "gripper" for call in control.calls)


def test_failed_staged_descent_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, stage_reached=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["stage_target"] == pytest.approx([0.15, 0.25, 0.845])
    assert result["stage_position_error"] == pytest.approx(0.1)
    assert result["stage_z_error"] == pytest.approx(0.1)
    assert result["stage_rotation_error_rad"] == pytest.approx(0.0)
    assert not any(call[0] == "gripper" for call in control.calls)


def test_measured_stage_z_error_within_position_tolerance_continues(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, stage_z_offset=0.012)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert result["released"] is True
    assert any(
        call[2].get("max_iters") == 150
        for call in servo_calls
    )


def test_measured_stage_xy_error_uses_requested_position_tolerance(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, stage_xy_offset=(0.01, 0.0))
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.02},
    )

    assert result["ok"] is True
    assert result["released"] is True


def test_hold_lost_after_stage_stops_before_final_descent(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(
        env,
        hold_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.CLOSED_EMPTY,
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is False
    assert result["released"] is False
    assert result["hold_check_stage"] == "staged_descent"
    assert len(servo_calls) == 2


def test_source_reoccupied_after_stage_stops_before_final_descent(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, reoccupy_after_stage=True)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is False
    assert result["released"] is False
    assert result["hold_check_stage"] == "staged_descent"
    assert result["source_clear_reason"] == "source_patch_reoccupied"
    assert len(servo_calls) == 2


def test_measured_pose_mismatch_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, update_descent_pose=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["reached"] is False
    assert result["pre_release_error"] >= 0.02
    assert not any(call[0] == "gripper" for call in control.calls)


def test_z_error_between_eight_and_twenty_mm_is_corrected(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, final_z_offset=0.012)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.02},
    )

    assert result["ok"] is True
    assert result["released"] is True
    assert result["final_correction_attempted"] is True
    assert result["pre_release_z_error"] <= 0.008
    assert any(
        call[0] == "correction"
        for call in control.calls
    )


def test_z_error_above_position_tolerance_never_opens_gripper(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, final_z_offset=0.03)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12], "pos_tol": 0.02},
    )

    assert result["ok"] is False
    assert result["released"] is False
    assert result["pre_release_z_error"] == pytest.approx(0.03)
    assert not any(call[0] == "gripper" for call in control.calls)


def test_invalid_final_pose_rejects_without_nonfinite_metrics(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, invalid_final_pose=True)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["pre_release_error"] is None
    assert result["pre_release_z_error"] is None
    assert result["ee_pos"] is None
    assert not any(call[0] == "gripper" for call in control.calls)


def test_lost_hold_before_release_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(
        env,
        hold_states=[
            GripperState.HELD,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.CLOSED_EMPTY,
        ],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_state_pre_release"] == "closed_empty"
    assert result["gripper_hold_continuity"] is False
    assert not any(call[0] == "gripper" for call in control.calls)


def test_missing_visual_hold_evidence_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    clear_visual_hold(env)
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["source_clear_verified"] is False
    assert control.calls == []


def test_source_reoccupied_never_opens_gripper(monkeypatch) -> None:
    env = FakeEnv()
    env.snapshot.images["head_camera"] = np.zeros(
        (32, 32, 3),
        dtype=np.uint8,
    )
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["source_clear_verified"] is False
    assert result["source_clear_reason"] == "source_patch_reoccupied"
    assert control.calls == []


def test_success_preserves_orientation_releases_and_retracts(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {
            "pixel": [10, 12],
            "release_clearance": 0.025,
            "hover": 0.12,
            "pos_tol": 0.02,
        },
    )

    servo_calls = [call for call in control.calls if call[0] == "servo"]
    assert len(servo_calls) == 4
    assert all(np.allclose(call[2]["quat"], control.quat) for call in servo_calls)
    approach = next(call for call in servo_calls if call[2].get("via_trajopt"))
    stage = next(
        call
        for call in servo_calls
        if (
            call[2].get("max_iters") == 80
            and call[2].get("gripper") == "close"
        )
    )
    final_descent = next(
        call for call in servo_calls if call[2].get("max_iters") == 150
    )
    assert approach[2]["pos_tol"] == pytest.approx(0.015)
    assert stage[2]["pos_tol"] == pytest.approx(0.02)
    assert final_descent[2]["pos_tol"] == pytest.approx(0.02)
    assert result["ok"] is True
    assert result["reached"] is True
    assert result["released"] is True
    assert result["gripper_opened"] is True
    assert result["object_release_verified"] is None
    assert result["source_clear_verified"] is True
    assert result["gripper_hold_continuity"] is True
    assert result["target_world"] == pytest.approx([0.15, 0.25, 0.80])


def test_surface_place_compensates_held_object_center_offset(
    monkeypatch,
) -> None:
    env = FakeEnv()
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    record_visual_hold(
        env,
        object_name="akita black bowl",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
        object_offset_local=(0.03, -0.04, 0.0),
    )
    env.snapshot.images["head_camera"] = after
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    control.quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=float)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12]},
    )

    final_descent = next(
        call
        for call in control.calls
        if call[0] == "servo" and call[2].get("max_iters") == 150
    )
    assert final_descent[1][:2] == pytest.approx([0.12, 0.29])
    assert result["released"] is True
    assert result["held_object_offset_xy"] == pytest.approx(
        [0.03, -0.04]
    )


def test_surface_place_rotates_local_object_offset_with_current_quat(
    monkeypatch,
) -> None:
    env = FakeEnv()
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    record_visual_hold(
        env,
        object_name="akita black bowl",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
        object_offset_local=(0.03, 0.0, 0.0),
    )
    env.snapshot.images["head_camera"] = after
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    control.quat = np.array([0.0, 0.0, 1.0, 0.0], dtype=float)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12]},
    )

    final_descent = next(
        call
        for call in control.calls
        if call[0] == "servo" and call[2].get("max_iters") == 150
    )
    assert final_descent[1][:2] == pytest.approx([0.18, 0.25])
    assert result["held_object_offset_xy"] == pytest.approx(
        [-0.03, 0.0]
    )


def test_surface_place_rotates_local_vertical_offset_into_world_xy(
    monkeypatch,
) -> None:
    env = FakeEnv()
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    record_visual_hold(
        env,
        object_name="akita black bowl",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
        object_offset_local=(0.0, 0.0, 0.04),
    )
    env.snapshot.images["head_camera"] = after
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    angle = np.pi / 4.0
    control.quat = np.array(
        [0.0, np.sin(angle), 0.0, np.cos(angle)],
        dtype=float,
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(
        state,
        {"pixel": [10, 12]},
    )

    final_descent = next(
        call
        for call in control.calls
        if call[0] == "servo" and call[2].get("max_iters") == 150
    )
    assert final_descent[1][:2] == pytest.approx([0.11, 0.25])
    assert result["held_object_offset_xy"] == pytest.approx(
        [0.04, 0.0]
    )


def test_release_open_is_retried_once(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(
        env,
        open_states=[GripperState.HELD, GripperState.OPEN],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert control.open_calls == 2
    assert result["released"] is True


def test_target_resolution_failure_does_not_move(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_resolve_surface_target",
        lambda *args, **kwargs: None,
    )

    result, _ = policy.dispatch_runtime(state, {"target": "white plate"})

    assert result["ok"] is False
    assert result["released"] is False
    assert control.calls == []


def test_invalid_entry_orientation_refuses_before_target_motion(
    monkeypatch,
) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    control.quat = np.array([np.nan, 0.0, 0.0, 1.0], dtype=float)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"target": "white plate"})

    assert result["ok"] is False
    assert result["released"] is False
    assert result["reason"] == "current end-effector orientation is invalid"
    assert control.calls == []


def test_second_open_failure_reports_not_released(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(
        env,
        open_states=[GripperState.HELD, GripperState.HELD],
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert control.open_calls == 2
    assert result["ok"] is False
    assert result["released"] is False
    assert result["gripper_opened"] is False
    assert result["object_release_verified"] is None


def test_retraction_failure_reports_released_true(monkeypatch) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env, retract_reached=False)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    _patch_target(monkeypatch)

    result, _ = policy.dispatch_runtime(state, {"pixel": [10, 12]})

    assert result["ok"] is False
    assert result["reached"] is True
    assert result["released"] is True
    assert result["gripper_opened"] is True
    assert result["object_release_verified"] is None
    assert "retract" in result["reason"]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(-1.0, 0.01), (0.025, 0.025), (1.0, 0.05)],
)
def test_release_clearance_is_clamped(monkeypatch, requested, expected) -> None:
    env = FakeEnv()
    state = SimpleNamespace(env=env)
    control = FakeControl(env)
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    target = _patch_target(monkeypatch)

    policy.dispatch_runtime(
        state,
        {
            "pixel": [10, 12],
            "release_clearance": requested,
            "hover": 0.12,
        },
    )

    release_call = next(
        call
        for call in control.calls
        if call[0] == "servo" and call[2].get("max_iters") == 150
    )
    assert release_call[1][2] == pytest.approx(target.world[2] + expected)


def test_place_on_surface_has_no_ground_truth_paths() -> None:
    root = Path(__file__).resolve().parents[3]
    policy_path = (
        root
        / "roborsi/embodied/skills/base/place_on_surface/libero/policy.py"
    )
    skill_path = policy_path.with_name("SKILL.md")
    assert scan_policy_path("place_on_surface", policy_path) == []
    assert scan_skill_doc_path("place_on_surface", skill_path) == []


def test_place_on_surface_is_wired_into_libero_tool_specs() -> None:
    specs = {
        row["function"]["name"]: row["function"]
        for row in _build_tool_specs(ns="libero", task="libero_pick_place")
    }
    assert "place_on_surface" in specs
    properties = specs["place_on_surface"]["parameters"]["properties"]
    assert set(properties) == {
        "target",
        "pixel",
        "release_clearance",
        "hover",
        "pos_tol",
    }
