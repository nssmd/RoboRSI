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
        "roborsi.embodied.skills.base.pull_drawer.libero.policy"
    )


class _Env:
    def take_snapshot(self):
        return SimpleNamespace(
            images={
                "head_camera": np.zeros((100, 100, 3), dtype=np.uint8)
            }
        )

    def pixel_to_world(self, u, v, camera="agentview"):
        _ = camera
        return np.array(
            [0.20, (float(u) - 50.0) * 0.001, 1.00 - (float(v) - 50.0) * 0.001],
            dtype=float,
        )

    def robot_base_pos(self):
        return np.array([-0.66, 0.0, 0.912], dtype=float)

    def raw_obs(self):
        raise AssertionError("drawer skill must not read hidden simulator state")


class _Control:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []
        self.pose = np.array([-0.20, 0.0, 1.10], dtype=float)

    def read_pose(self):
        return (
            self.pose.copy(),
            np.array([0.0, 0.0, 0.0, 1.0]),
            np.zeros(2),
        )

    def read_gripper_state(self):
        state = self.states.pop(0)
        gap = 0.02 if state is GripperState.HELD else 0.001
        return gap, state

    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        self.pose = target
        self.calls.append(("servo", target, kwargs))
        return True, None

    def set_gripper(self, *, close, steps=12):
        self.calls.append(("gripper", close, steps))


def test_pull_drawer_approaches_and_pulls_toward_robot(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    control = _Control(
        [
            GripperState.OPEN,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ]
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.0], dtype=float),
            np.array([-1.0, 0.0, 0.0], dtype=float),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        policy,
        "_visual_handle_pull",
        lambda *args: (0.08, (55, 50)),
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "middle drawer handle",
            "pixel": [50, 50],
            "pull_distance": 0.12,
            "approach": 0.10,
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert result["pulled"] is True
    assert result["pull_distance"] >= 0.10
    assert len(servos) == 4
    approach, contact, pull, retract = [call[1] for call in servos]
    assert approach[0] < contact[0]
    handle_surface = np.array([0.20, 0.0, 1.0], dtype=float)
    outward_normal = np.array([-1.0, 0.0, 0.0], dtype=float)
    assert float(np.dot(contact - handle_surface, outward_normal)) < 0.0
    assert pull[0] < contact[0]
    assert retract[0] < pull[0]
    assert retract[2] > pull[2]
    assert all("quat" in call[2] for call in servos)
    assert any(
        call[0] == "gripper" and call[1] is True
        for call in control.calls
    )
    assert any(
        call[0] == "gripper" and call[1] is False
        for call in control.calls
    )
    evidence = getattr(env, "_libero_drawer_pull_evidence", None)
    assert evidence is not None
    assert evidence.layer == "middle"
    assert evidence.handle_point_before == pytest.approx([0.20, 0.0, 1.0])
    assert evidence.face_normal == pytest.approx([-1.0, 0.0, 0.0])
    assert evidence.achieved_pull_distance == pytest.approx(
        result["pull_distance"]
    )


def test_drawer_pull_distance_enforces_predicate_sized_motion() -> None:
    policy = _policy()

    assert policy._drawer_pull_distance(0.06) == pytest.approx(0.18)
    assert policy._drawer_pull_distance(0.12) == pytest.approx(0.18)
    assert policy._drawer_pull_distance(0.18) == pytest.approx(0.18)


def test_drawer_motion_evidence_rejects_subthreshold_pull() -> None:
    policy = _policy()

    assert policy._motion_pull_verified(0.149, GripperState.HELD) is False
    assert policy._motion_pull_verified(0.150, GripperState.HELD) is True
    assert policy._motion_pull_verified(0.180, GripperState.CLOSED_EMPTY) is False


def test_measured_pull_survives_missing_post_visual_relocalization(
    monkeypatch,
) -> None:
    policy = _policy()
    control = _Control(
        [
            GripperState.OPEN,
            GripperState.HELD,
            GripperState.HELD,
            GripperState.OPEN,
        ]
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.0], dtype=float),
            np.array([-1.0, 0.0, 0.0], dtype=float),
        ),
    )
    monkeypatch.setattr(
        policy,
        "_visual_handle_pull",
        lambda *args: (None, None),
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "top drawer handle",
            "pixel": [50, 50],
            "pull_distance": 0.12,
        },
    )

    assert result["ok"] is True
    assert result["pulled"] is True
    assert result["motion_pull_verified"] is True
    assert result["visual_motion_verified"] is False


def test_drawer_release_accepts_near_open_gap_after_open_command(
    monkeypatch,
) -> None:
    policy = _policy()

    class _NearOpenControl:
        def __init__(self):
            self.commands = 0

        def set_gripper(self, *, close, steps=12):
            _ = steps
            assert close is False
            self.commands += 1

        def read_gripper_state(self):
            return 0.0782, GripperState.AMBIGUOUS

    control = _NearOpenControl()

    gap, state = policy._open(control)

    assert control.commands == 2
    assert gap == pytest.approx(0.0782)
    assert state is GripperState.OPEN


def test_pull_drawer_does_not_pull_after_empty_close(
    monkeypatch,
) -> None:
    policy = _policy()
    control = _Control(
        [
            GripperState.OPEN,
            GripperState.CLOSED_EMPTY,
            GripperState.OPEN,
        ]
    )
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (50, 50),
            np.array([0.20, 0.0, 1.0], dtype=float),
            np.array([-1.0, 0.0, 0.0], dtype=float),
        ),
        raising=False,
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "bottom drawer handle",
            "pixel": [50, 50],
        },
    )

    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is False
    assert result["pulled"] is False
    assert len(servos) == 3
    assert any(
        call[0] == "gripper" and call[1] is False
        for call in control.calls
    )


def test_pull_drawer_refuses_while_gripper_is_holding(
    monkeypatch,
) -> None:
    policy = _policy()
    control = _Control([GripperState.HELD])
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "middle drawer handle",
            "pixel": [50, 50],
        },
    )

    assert result["ok"] is False
    assert result["pulled"] is False
    assert control.calls == []


def test_pull_drawer_rejects_non_drawer_target(
    monkeypatch,
) -> None:
    policy = _policy()
    control = _Control([GripperState.OPEN])
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "green bottle",
            "pixel": [50, 50],
        },
    )

    assert result["ok"] is False
    assert result["pulled"] is False
    assert control.calls == []


def test_pull_drawer_rejects_semantically_wrong_pixel(
    monkeypatch,
) -> None:
    policy = _policy()
    control = _Control([GripperState.OPEN])
    monkeypatch.setattr(policy, "LiberoControl", lambda env: control)
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: None,
        raising=False,
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {
            "object": "middle drawer handle",
            "pixel": [50, 50],
        },
    )

    assert result["ok"] is False
    assert result["pulled"] is False
    assert "visually verified" in result["reason"]
    assert control.calls == []


def test_visual_handle_pull_rejects_static_relocalized_handle(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    state = SimpleNamespace(env=env)
    monkeypatch.setattr(
        "roborsi.embodied.skills.base._lib.libero._perception.localize_precise",
        lambda *args, **kwargs: (50, 50),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda state, object_name, rgb, uv: (
            (int(uv[0]), int(uv[1])),
            np.array([0.20, 0.0, 1.0]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )

    distance, uv = policy._visual_handle_pull(
        state,
        "middle drawer handle",
        np.array([0.20, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    )

    assert distance == 0.0
    assert uv == (50, 50)


def test_visual_handle_pull_accepts_same_handle_outward_motion(
    monkeypatch,
) -> None:
    policy = _policy()

    class _MovedEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (v, camera)
            if int(u) == 60:
                return np.array([0.10, 0.0, 1.0])
            return np.array([0.20, 0.0, 1.0])

    state = SimpleNamespace(env=_MovedEnv())
    monkeypatch.setattr(
        "roborsi.embodied.skills.base._lib.libero._perception.localize_precise",
        lambda *args, **kwargs: (60, 50),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda state, object_name, rgb, uv: (
            (int(uv[0]), int(uv[1])),
            np.array([0.10, 0.0, 1.0]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )

    distance, uv = policy._visual_handle_pull(
        state,
        "middle drawer handle",
        np.array([0.20, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    )

    assert distance == 0.10
    assert uv == (60, 50)


def test_visual_handle_pull_rejects_wrong_instance_transverse_jump(
    monkeypatch,
) -> None:
    policy = _policy()

    class _WrongEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (u, v, camera)
            return np.array([0.10, 0.20, 1.10])

    state = SimpleNamespace(env=_WrongEnv())
    monkeypatch.setattr(
        "roborsi.embodied.skills.base._lib.libero._perception.localize_precise",
        lambda *args, **kwargs: (60, 50),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda state, object_name, rgb, uv: (
            (int(uv[0]), int(uv[1])),
            np.array([0.10, 0.20, 1.10]),
            np.array([-1.0, 0.0, 0.0]),
        ),
    )

    distance, uv = policy._visual_handle_pull(
        state,
        "middle drawer handle",
        np.array([0.20, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    )

    assert distance is None
    assert uv == (60, 50)


def test_face_normal_robustly_rejects_depth_outliers() -> None:
    policy = _policy()

    class _OutlierEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            if (int(u), int(v)) in {
                (34, 34),
                (66, 66),
                (74, 50),
            }:
                return np.array([-0.40, 0.30, 0.80])
            return np.array(
                [
                    0.20,
                    (float(u) - 50.0) * 0.002,
                    1.00 - (float(v) - 50.0) * 0.002,
                ]
            )

    normal = policy._face_normal(
        _OutlierEnv(),
        50,
        50,
        np.array([0.20, 0.0, 1.0]),
        (100, 100, 3),
    )

    assert normal is not None
    assert normal[0] < -0.9
    assert abs(normal[2]) < 1e-6


def test_face_normal_uses_robot_vector_only_for_sign() -> None:
    policy = _policy()

    class _YPlaneEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            return np.array(
                [
                    0.0528 + (float(u) - 50.0) * 0.002,
                    -0.1371,
                    0.9559 - (float(v) - 50.0) * 0.002,
                ]
            )

    normal = policy._face_normal(
        _YPlaneEnv(),
        50,
        50,
        np.array([0.0528, -0.1371, 0.9559]),
        (100, 100, 3),
    )

    assert normal is not None
    assert normal[1] > 0.9
    assert abs(normal[2]) < 1e-6


def test_face_normal_rejects_ambiguous_sign_near_orthogonal() -> None:
    policy = _policy()

    class _AmbiguousYPlaneEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            return np.array(
                [
                    0.0528 + (float(u) - 50.0) * 0.002,
                    -0.1371,
                    0.9559 - (float(v) - 50.0) * 0.002,
                ]
            )

        def robot_base_pos(self):
            return np.array([-0.66, -0.1371, 0.912], dtype=float)

    assert (
        policy._face_normal(
            _AmbiguousYPlaneEnv(),
            50,
            50,
            np.array([0.0528, -0.1371, 0.9559]),
            (100, 100, 3),
        )
        is None
    )


def test_refine_drawer_handle_finds_supported_protruding_cluster() -> None:
    policy = _policy()

    class _HandleEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            x = 0.16 if abs(int(u) - 52) <= 1 and abs(int(v) - 49) <= 1 else 0.20
            return np.array(
                [
                    x,
                    (float(u) - 50.0) * 0.002,
                    1.00 - (float(v) - 50.0) * 0.002,
                ],
                dtype=float,
            )

    refined = policy._refine_drawer_handle_geometry(
        _HandleEnv(),
        50,
        50,
        (100, 100, 3),
    )

    assert refined is not None
    uv, point, normal = refined
    assert uv == (52, 49)
    assert point[0] == 0.16
    assert normal[0] < -0.9


def test_refine_drawer_handle_recovers_from_sparse_grazing_samples() -> None:
    policy = _policy()

    class _GrazingDepthEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            du = int(u) - 50
            dv = int(v) - 50
            sparse_offsets = {
                (x, y)
                for radius in (10, 16, 24)
                for x, y in (
                    (-radius, -radius),
                    (0, -radius),
                    (radius, -radius),
                    (-radius, 0),
                    (radius, 0),
                    (-radius, radius),
                    (0, radius),
                    (radius, radius),
                )
            }
            if (du, dv) in sparse_offsets:
                return None
            y = -0.14 if 0 <= du <= 6 and abs(dv) <= 1 else -0.18
            return np.array(
                [
                    0.20 + float(du) * 0.002,
                    y,
                    1.00 - float(dv) * 0.002,
                ],
                dtype=float,
            )

    refined = policy._refine_drawer_handle_geometry(
        _GrazingDepthEnv(),
        50,
        50,
        (100, 100, 3),
    )

    assert refined is not None
    uv, point, normal = refined
    assert uv == (53, 50)
    assert point[1] == -0.14
    assert normal[1] > 0.9


def test_refine_drawer_handle_targets_connected_component_center() -> None:
    policy = _policy()

    class _WideHandleEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            du = int(u) - 50
            dv = int(v) - 50
            y = -0.14 if 0 <= du <= 8 and abs(dv) <= 1 else -0.18
            return np.array(
                [
                    0.20 + float(du) * 0.002,
                    y,
                    1.00 - float(dv) * 0.002,
                ],
                dtype=float,
            )

    refined = policy._refine_drawer_handle_geometry(
        _WideHandleEnv(),
        50,
        50,
        (100, 100, 3),
    )

    assert refined is not None
    uv, point, normal = refined
    assert uv == (54, 50)
    assert point[1] == -0.14
    assert normal[1] > 0.9


def test_refine_drawer_handle_prefers_medoid_over_depth_peak() -> None:
    policy = _policy()

    class _NoisyWideHandleEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            du = int(u) - 50
            dv = int(v) - 50
            if 0 <= du <= 8 and abs(dv) <= 1:
                y = -0.12 if du == 8 else -0.14
            else:
                y = -0.18
            return np.array(
                [
                    0.20 + float(du) * 0.002,
                    y,
                    1.00 - float(dv) * 0.002,
                ],
                dtype=float,
            )

    refined = policy._refine_drawer_handle_geometry(
        _NoisyWideHandleEnv(),
        50,
        50,
        (100, 100, 3),
    )

    assert refined is not None
    uv, point, _ = refined
    assert uv == (54, 50)
    assert point[1] == -0.14


def test_layer_handle_candidates_rank_requested_layer(monkeypatch) -> None:
    policy = _policy()

    def _refine(env, u, v, image_shape):
        _ = (env, u, image_shape)
        handle_v = min((30, 50, 70), key=lambda value: abs(value - int(v)))
        if abs(handle_v - int(v)) > 7:
            return None
        return (
            (50, handle_v),
            np.array([0.20, 0.0, 1.00 - handle_v * 0.001]),
            np.array([-1.0, 0.0, 0.0]),
        )

    monkeypatch.setattr(policy, "_refine_drawer_handle_geometry", _refine)

    top = policy._layer_handle_candidates(_Env(), 50, 50, (100, 100, 3), "top")
    middle = policy._layer_handle_candidates(
        _Env(), 50, 50, (100, 100, 3), "middle"
    )
    bottom = policy._layer_handle_candidates(
        _Env(), 50, 50, (100, 100, 3), "bottom"
    )

    assert top[0][0] == (50, 30)
    assert middle[0][0] == (50, 50)
    assert bottom[0][0] == (50, 70)


def test_layer_handle_candidates_follow_shifted_handle_stack() -> None:
    policy = _policy()

    class _StackedHandleEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            handle = (
                (35 <= int(u) <= 41 and abs(int(v) - 30) <= 1)
                or (47 <= int(u) <= 53 and abs(int(v) - 50) <= 1)
                or (59 <= int(u) <= 65 and abs(int(v) - 70) <= 1)
            )
            return np.array(
                [
                    0.20 + (float(u) - 50.0) * 0.001,
                    -0.14 if handle else -0.18,
                    1.00 - (float(v) - 50.0) * 0.001,
                ],
                dtype=float,
            )

    env = _StackedHandleEnv()
    top = policy._layer_handle_candidates(env, 50, 50, (100, 100, 3), "top")
    middle = policy._layer_handle_candidates(
        env, 50, 50, (100, 100, 3), "middle"
    )
    bottom = policy._layer_handle_candidates(
        env, 50, 50, (100, 100, 3), "bottom"
    )

    assert top[0][0] == (38, 30)
    assert middle[0][0] == (50, 50)
    assert bottom[0][0] == (62, 70)


def test_layer_handle_candidates_augment_incomplete_wide_scan(
    monkeypatch,
) -> None:
    policy = _policy()
    top = (
        (50, 30),
        np.array([0.20, 0.0, 1.09]),
        np.array([-1.0, 0.0, 0.0]),
    )
    monkeypatch.setattr(
        policy,
        "_wide_handle_components",
        lambda *args: [top],
    )

    def _refine(env, u, v, image_shape):
        _ = (env, u, image_shape)
        handle_v = min((30, 50, 70), key=lambda value: abs(value - int(v)))
        if abs(handle_v - int(v)) > 7:
            return None
        return (
            (50, handle_v),
            np.array([0.20, 0.0, 1.12 - handle_v * 0.001]),
            np.array([-1.0, 0.0, 0.0]),
        )

    monkeypatch.setattr(policy, "_refine_drawer_handle_geometry", _refine)

    candidates = policy._layer_handle_candidates(
        _Env(),
        50,
        30,
        (100, 100, 3),
        "middle",
    )

    assert candidates[0][0] == (50, 50)


def test_resolve_drawer_handle_recovers_wrong_layer(monkeypatch) -> None:
    policy = _policy()
    initial = (
        (50, 50),
        np.array([0.20, 0.0, 0.95]),
        np.array([-1.0, 0.0, 0.0]),
    )
    corrected = (
        (50, 30),
        np.array([0.20, 0.0, 0.97]),
        np.array([-1.0, 0.0, 0.0]),
    )
    monkeypatch.setattr(
        policy,
        "_refine_drawer_handle_geometry",
        lambda *args: initial,
    )
    monkeypatch.setattr(
        policy,
        "_layer_handle_candidates",
        lambda *args: [corrected, initial],
        raising=False,
    )
    monkeypatch.setattr(
        policy,
        "_verify_drawer_handle_pixel",
        lambda state, object_name, rgb, uv: uv == corrected[0],
    )

    resolved = policy._resolve_drawer_handle(
        SimpleNamespace(env=_Env()),
        "top drawer handle",
        np.zeros((100, 100, 3), dtype=np.uint8),
        (50, 50),
    )

    assert resolved is not None
    assert resolved[0] == corrected[0]
    assert resolved[1] == pytest.approx(corrected[1])
    assert resolved[2] == pytest.approx(corrected[2])


def test_resolve_drawer_handle_prefers_geometric_layer_over_false_positive(
    monkeypatch,
) -> None:
    policy = _policy()
    wrong_top = (
        (50, 30),
        np.array([0.20, 0.0, 1.09]),
        np.array([-1.0, 0.0, 0.0]),
    )
    correct_middle = (
        (50, 50),
        np.array([0.20, 0.0, 1.02]),
        np.array([-1.0, 0.0, 0.0]),
    )
    monkeypatch.setattr(
        policy,
        "_refine_drawer_handle_geometry",
        lambda *args: wrong_top,
    )
    monkeypatch.setattr(
        policy,
        "_layer_handle_candidates",
        lambda *args: [correct_middle, wrong_top],
    )
    monkeypatch.setattr(
        policy,
        "_verify_drawer_handle_pixel",
        lambda *args: True,
    )

    resolved = policy._resolve_drawer_handle(
        SimpleNamespace(env=_Env()),
        "middle drawer handle",
        np.zeros((100, 100, 3), dtype=np.uint8),
        wrong_top[0],
    )

    assert resolved is not None
    assert resolved[0] == correct_middle[0]
    assert resolved[1] == pytest.approx(correct_middle[1])


def test_refine_drawer_handle_rejects_flat_panel() -> None:
    policy = _policy()

    assert (
        policy._refine_drawer_handle_geometry(
            _Env(),
            50,
            50,
            (100, 100, 3),
        )
        is None
    )


def test_refine_drawer_handle_rejects_single_depth_spike() -> None:
    policy = _policy()

    class _SpikeEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            point = super().pixel_to_world(u, v, camera=camera)
            if (int(u), int(v)) == (52, 49):
                point[0] = 0.16
            return point

    assert (
        policy._refine_drawer_handle_geometry(
            _SpikeEnv(),
            50,
            50,
            (100, 100, 3),
        )
        is None
    )


def test_drawer_verifier_requires_correct_layer(monkeypatch, tmp_path) -> None:
    policy = _policy()
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.vlm_io._call_vlm_image",
        lambda *args, **kwargs: (
            '{"attached_handle": true, "correct_drawer_layer": false, '
            '"pixel_on_handle": true, "confidence": 0.99}'
        ),
    )

    matched = policy._verify_drawer_handle_pixel(
        SimpleNamespace(env=_Env(), workdir=tmp_path),
        "middle drawer handle",
        np.zeros((100, 100, 3), dtype=np.uint8),
        (50, 50),
    )

    assert matched is False


def test_drawer_verifier_accepts_exact_handle_at_medium_confidence(
    monkeypatch,
    tmp_path,
) -> None:
    policy = _policy()
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.vlm_io._call_vlm_image",
        lambda *args, **kwargs: (
            '{"attached_handle": true, "correct_drawer_layer": true, '
            '"pixel_on_handle": true, "confidence": 0.70}'
        ),
    )

    matched = policy._verify_drawer_handle_pixel(
        SimpleNamespace(env=_Env(), workdir=tmp_path),
        "top drawer handle",
        np.zeros((100, 100, 3), dtype=np.uint8),
        (50, 50),
    )

    assert matched is True


def test_resolve_drawer_handle_uses_one_vlm_pixel_correction(monkeypatch) -> None:
    policy = _policy()
    initial = (
        (50, 50),
        np.array([0.20, 0.0, 0.95]),
        np.array([-1.0, 0.0, 0.0]),
    )
    corrected = (
        (60, 40),
        np.array([0.20, 0.0, 0.97]),
        np.array([-1.0, 0.0, 0.0]),
    )

    def _refine(env, u, v, image_shape):
        _ = (env, image_shape)
        if (int(u), int(v)) == corrected[0]:
            raise AssertionError("corrected pixel must not be re-centered")
        return initial

    monkeypatch.setattr(policy, "_refine_drawer_handle_geometry", _refine)
    monkeypatch.setattr(policy, "_layer_handle_candidates", lambda *args: [])
    monkeypatch.setattr(
        policy,
        "_verify_drawer_handle_pixel",
        lambda state, object_name, rgb, uv: uv == corrected[0],
    )
    monkeypatch.setattr(
        policy,
        "_corrected_drawer_handle_pixel",
        lambda *args: corrected[0],
        raising=False,
    )
    monkeypatch.setattr(
        policy,
        "_point",
        lambda env, u, v, **kwargs: corrected[1]
        if (int(u), int(v)) == corrected[0]
        else initial[1],
    )

    resolved = policy._resolve_drawer_handle(
        SimpleNamespace(env=_Env()),
        "top drawer handle",
        np.zeros((100, 100, 3), dtype=np.uint8),
        initial[0],
    )

    assert resolved is not None
    assert resolved[0] == corrected[0]
    assert resolved[1] == pytest.approx(corrected[1])
    assert resolved[2] == pytest.approx(corrected[2])


def test_refine_drawer_handle_rejects_tilted_wood_surface() -> None:
    policy = _policy()

    class _TiltedEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = camera
            dv = (float(v) - 50.0) * 0.002
            return np.array(
                [
                    0.20 + dv,
                    (float(u) - 50.0) * 0.002,
                    1.00 - dv,
                ],
                dtype=float,
            )

    assert (
        policy._refine_drawer_handle_geometry(
            _TiltedEnv(),
            50,
            50,
            (100, 100, 3),
        )
        is None
    )


def test_refine_drawer_handle_uses_supported_medoid_not_panel_center() -> None:
    policy = _policy()

    class _SplitHandleEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            point = super().pixel_to_world(u, v, camera=camera)
            if int(u) in {49, 51} and 49 <= int(v) <= 51:
                point[0] = 0.16
            return point

    refined = policy._refine_drawer_handle_geometry(
        _SplitHandleEnv(),
        50,
        50,
        (100, 100, 3),
    )

    assert refined is not None
    uv, point, _ = refined
    assert uv[0] in {49, 51}
    assert point[0] == 0.16


def test_visual_handle_pull_rejects_changed_face_normal(
    monkeypatch,
) -> None:
    policy = _policy()
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        "roborsi.embodied.skills.base._lib.libero._perception.localize_precise",
        lambda *args, **kwargs: (60, 50),
    )
    monkeypatch.setattr(
        policy,
        "_resolve_drawer_handle",
        lambda *args: (
            (60, 50),
            np.array([0.10, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
        ),
    )

    distance, uv = policy._visual_handle_pull(
        state,
        "middle drawer handle",
        np.array([0.20, 0.0, 1.0]),
        np.array([-1.0, 0.0, 0.0]),
    )

    assert distance is None
    assert uv == (60, 50)


def test_drawer_verifier_edge_crop_is_padded_square() -> None:
    policy = _policy()
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    crop, center = policy._padded_square_crop(
        image,
        (9, 90),
        radius=24,
    )

    assert crop.shape == (48, 48, 3)
    assert center == (24, 24)
