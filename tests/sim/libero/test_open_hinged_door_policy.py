from __future__ import annotations

import importlib
from types import SimpleNamespace

import numpy as np
import pytest

from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
from roborsi.embodied.skills import get_ns
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)
from roborsi.embodied.skills.base._lib.libero.semantic_point import (
    record_semantic_point,
)


def _policy():
    return importlib.import_module(
        "roborsi.embodied.skills.base.open_hinged_door.libero.policy"
    )


class _Env:
    def __init__(self) -> None:
        self.frame = np.zeros((512, 512, 3), dtype=np.uint8)

    def take_snapshot(self):
        return SimpleNamespace(
            images={"head_camera": self.frame.copy()},
            state=np.zeros(1, dtype=np.float32),
        )

    def pixel_to_world(self, u, v, camera="agentview"):
        _ = (v, camera)
        if int(u) < 145:
            return np.array([0.20, -0.02, 0.80], dtype=float)
        return np.array(
            [0.20, (float(u) - 150.0) * 0.001, 1.00],
            dtype=float,
        )

    def robot_base_pos(self):
        return np.array([-0.66, 0.20, 0.912], dtype=float)

    def raw_obs(self):
        raise AssertionError("door skill must not read hidden simulator state")


class _Control:
    instances = []

    def __init__(self, env) -> None:
        self.env = env
        self.calls = []
        self.pose = np.array([-0.20, 0.20, 1.10], dtype=float)
        self.closed = False
        self.instances.append(self)

    def read_pose(self):
        return (
            self.pose.copy(),
            np.array([0.0, 0.0, 0.0, 1.0], dtype=float),
            np.zeros(2, dtype=float),
        )

    def read_gripper_state(self):
        if self.closed:
            return 0.03, GripperState.HELD
        return 0.08, GripperState.OPEN

    def set_gripper(self, *, close, steps=12):
        self.closed = bool(close)
        self.calls.append(("gripper", bool(close), int(steps)))

    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        self.pose = target
        self.calls.append(("servo", target, kwargs))
        return True, None


def test_open_hinged_door_skill_is_registered() -> None:
    policy = _policy()

    assert policy is not None
    assert get_ns("open_hinged_door", "libero") is not None


def test_open_hinged_door_exposes_numeric_tool_schema() -> None:
    specs = {
        row["function"]["name"]: row["function"]
        for row in _build_tool_specs(
            ns="libero",
            task="libero_pick_place",
        )
    }
    properties = specs["open_hinged_door"]["parameters"]["properties"]

    assert properties["pixel"]["type"] == "array"
    assert properties["angle_deg"]["type"] == "number"
    assert properties["approach"]["type"] == "number"


def test_hinge_inference_scales_with_image_width_and_uses_far_edge() -> None:
    policy = _policy()
    env = _Env()
    handle = env.pixel_to_world(350, 250)

    hinge = policy._infer_hinge_point(
        env,
        handle_pixel=(350, 250),
        handle_point=handle,
        image_shape=env.frame.shape,
        hinge_side="left",
    )

    assert hinge is not None
    assert hinge[0] == pytest.approx(0.20)
    assert hinge[1] < 0.03
    assert 0.17 <= np.linalg.norm((handle - hinge)[:2]) <= 0.24


def test_attached_handle_refinement_accepts_nearby_geometry(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    raw = np.array([0.20, 0.20, 1.00], dtype=float)
    monkeypatch.setattr(
        policy,
        "_drawer_handle_refinement",
        lambda *args, **kwargs: (
            (358, 246),
            np.array([0.21, 0.19, 1.05], dtype=float),
            np.array([0.0, 1.0, 0.0], dtype=float),
        ),
    )

    uv, point, normal = policy._resolve_attached_handle(
        env,
        pixel=(350, 250),
        raw_point=raw,
        image_shape=env.frame.shape,
    )

    assert uv == (358, 246)
    assert point == pytest.approx([0.21, 0.19, 1.05])
    assert normal is not None
    toward_robot = env.robot_base_pos() - point
    toward_robot[2] = 0.0
    toward_robot /= np.linalg.norm(toward_robot)
    assert float(np.dot(normal, toward_robot)) > 0.99


def test_attached_handle_refinement_rejects_large_world_jump(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    raw = np.array([0.20, 0.20, 1.00], dtype=float)
    monkeypatch.setattr(
        policy,
        "_drawer_handle_refinement",
        lambda *args, **kwargs: (
            (352, 248),
            np.array([-0.20, 0.20, 0.75], dtype=float),
            np.array([-1.0, 0.0, 0.0], dtype=float),
        ),
    )

    uv, point, _ = policy._resolve_attached_handle(
        env,
        pixel=(350, 250),
        raw_point=raw,
        image_shape=env.frame.shape,
    )

    assert uv == (350, 250)
    assert point == pytest.approx(raw)


def test_door_motion_requires_matching_current_semantic_point(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    monkeypatch.setattr(policy, "LiberoControl", _Control)

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "microwave door handle",
            "pixel": [350, 250],
            "hinge_side": "left",
        },
    )

    assert result["ok"] is False
    assert result["opened"] is False
    assert "semantic" in result["reason"]
    assert _Control.instances == []


def test_open_hinged_door_follows_visual_hinge_arc(monkeypatch) -> None:
    policy = _policy()
    env = _Env()
    record_semantic_point(
        env,
        object_name="microwave door handle",
        pixel=(350, 250),
        frame=env.frame,
        source="vlm->sam",
    )
    _Control.instances.clear()
    monkeypatch.setattr(policy, "LiberoControl", _Control)
    monkeypatch.setattr(
        policy,
        "_door_face_normal",
        lambda *args, **kwargs: np.array([-1.0, 0.0, 0.0]),
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "microwave door handle",
            "pixel": [350, 250],
            "hinge_side": "left",
            "angle_deg": 60,
            "approach": 0.09,
        },
    )

    control = _Control.instances[0]
    servos = [call for call in control.calls if call[0] == "servo"]
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["achieved_angle_deg"] >= 50.0
    assert result["hinge_radius"] >= 0.17
    assert len(servos) >= 10
    assert servos[0][1][0] < 0.20
    assert any(
        call[0] == "gripper" and call[1] is True
        for call in control.calls
    )
    assert control.calls[-2][0:2] == ("gripper", False)


def test_open_hinged_door_never_closes_when_approach_fails(
    monkeypatch,
) -> None:
    policy = _policy()
    env = _Env()
    record_semantic_point(
        env,
        object_name="microwave door handle",
        pixel=(350, 250),
        frame=env.frame,
        source="vlm->sam",
    )

    class _BlockedControl(_Control):
        def servo_to(self, pos, **kwargs):
            target = np.asarray(pos, dtype=float)
            self.calls.append(("servo", target, kwargs))
            return False, None

    _BlockedControl.instances.clear()
    monkeypatch.setattr(policy, "LiberoControl", _BlockedControl)
    monkeypatch.setattr(
        policy,
        "_door_face_normal",
        lambda *args, **kwargs: np.array([-1.0, 0.0, 0.0]),
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "microwave door handle",
            "pixel": [350, 250],
            "hinge_side": "left",
        },
    )

    assert result["ok"] is False
    assert not any(
        call[0] == "gripper" and call[1] is True
        for call in _BlockedControl.instances[0].calls
    )
