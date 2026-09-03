from __future__ import annotations

from pathlib import Path
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
        return np.array([0.15, 0.25, 0.80], dtype=float)


def _container_cloud(
    center=(0.15, 0.25),
    half_extent=(0.04, 0.03),
    floor_z=0.78,
    rim_z=0.82,
) -> np.ndarray:
    cx, cy = center
    hx, hy = half_extent
    xs = np.linspace(cx - hx, cx + hx, 7)
    ys = np.linspace(cy - hy, cy + hy, 7)
    rows = []
    for z in np.linspace(floor_z, rim_z, 4):
        rows.extend((x, cy - hy, z) for x in xs)
        rows.extend((x, cy + hy, z) for x in xs)
        rows.extend((cx - hx, y, z) for y in ys)
        rows.extend((cx + hx, y, z) for y in ys)
    rows.extend((x, y, floor_z) for x in xs[1:-1] for y in ys[1:-1])
    return np.asarray(rows, dtype=float)


class _Control:
    def __init__(self, _env):
        self.pose = np.array([0.0, 0.0, 1.0], dtype=float)
        self.opened = False
        self.targets = []

    def read_pose(self):
        return (
            self.pose.copy(),
            np.array([0.0, 0.0, 0.0, 1.0], dtype=float),
            None,
        )

    def read_gripper_state(self):
        if self.opened:
            return 0.08, GripperState.OPEN
        return 0.02, GripperState.HELD

    def servo_to(self, pos, **_kwargs):
        self.targets.append(np.asarray(pos, dtype=float))
        self.pose = np.asarray(pos, dtype=float)
        return True, None

    def set_gripper(self, *, close, steps=12):
        self.opened = not close


@pytest.mark.parametrize("placeholder", [[], [0.0, 0.0, 0.0]])
def test_named_target_ignores_placeholder_pos_in_perception_mode(
    monkeypatch,
    placeholder,
) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    localized = {"calls": 0}

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _Control)
    monkeypatch.setattr(perception, "_fix_on", lambda: True)
    monkeypatch.setattr(perception, "_place_fix_on", lambda: False)
    monkeypatch.setattr(
        policy,
        "_clear_localize",
        lambda state, ctrl, name: localized.__setitem__(
            "calls", localized["calls"] + 1
        )
        or (20, 30),
    )
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda env, u, v, z_band: _container_cloud(
            center=(0.105, 0.205),
        ),
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "the white plate",
            "pos": placeholder,
            "z_offset": 0.06,
            "hover": 0.12,
        },
    )

    assert localized["calls"] == 1
    assert result["ok"] is True
    assert result["released"] is True
    assert result["placed"] is True


def test_skill_exposes_pixel_for_relational_place_targets() -> None:
    skill = Path(policy.__file__).with_name("SKILL.md").read_text()
    assert "pixel:" in skill
    assert "compartment" in skill


def test_perception_place_clamps_high_release_offset(monkeypatch) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(env):
        control = _Control(env)
        controls.append(control)
        return control

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)
    monkeypatch.setattr(perception, "_fix_on", lambda: True)
    monkeypatch.setattr(perception, "_place_fix_on", lambda: False)
    monkeypatch.setattr(policy, "_clear_localize", lambda state, ctrl, name: (20, 30))
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda env, u, v, z_band: _container_cloud(
            center=(0.105, 0.205),
        ),
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "back compartment of the caddy",
            "pixel": [20, 30],
            "z_offset": 0.12,
            "hover": 0.12,
        },
    )

    assert result["ok"] is True
    assert result["placed"] is True
    control = controls[0]
    rim_z = float(
        np.percentile(
            _container_cloud(center=(0.105, 0.205))[:, 2],
            85,
        )
    )
    assert control.targets[-3][2] == pytest.approx(rim_z + 0.12)
    assert control.targets[-2][2] == pytest.approx(rim_z + 0.06)


def test_relational_compartment_drop_keeps_visual_pixel_xy(monkeypatch) -> None:
    class _RelationalEnv(_Env):
        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (u, v, camera)
            return np.array([0.19, 0.25, 0.80], dtype=float)

    env = _RelationalEnv()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(value):
        control = _Control(value)
        controls.append(control)
        return control

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: _container_cloud(
            center=(0.15, 0.25),
            half_extent=(0.06, 0.04),
        ),
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "right compartment of the caddy",
            "pixel": [20, 30],
            "z_offset": 0.04,
            "hover": 0.12,
        },
    )

    assert result["released"] is True
    approach = controls[0].targets[0]
    assert approach[:2] == pytest.approx([0.19, 0.25])


def test_pixel_place_fails_closed_when_sam_cloud_is_missing(
    monkeypatch,
) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(env):
        control = _Control(env)
        controls.append(control)
        return control

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)
    monkeypatch.setattr(perception, "object_cloud", lambda *args, **kwargs: None)

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "back compartment of the caddy",
            "pixel": [20, 30],
            "z_offset": 0.04,
            "hover": 0.12,
        },
    )

    assert result["ok"] is False
    assert result["released"] is False
    assert "container rim" in result["reason"]
    assert controls[0].targets == []


def test_pixel_place_fails_closed_for_inconsistent_cloud(monkeypatch) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(env):
        control = _Control(env)
        controls.append(control)
        return control

    far_cloud = _container_cloud(
        center=(0.505, 0.545),
        floor_z=0.80,
        rim_z=0.84,
    )
    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: far_cloud,
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "back compartment of the caddy",
            "pixel": [20, 30],
            "z_offset": 0.04,
            "hover": 0.12,
        },
    )

    assert result["ok"] is False
    assert result["released"] is False
    assert "inconsistent" in result["reason"]
    assert controls[0].targets == []


def test_container_approach_stays_above_release_when_hover_is_low(
    monkeypatch,
) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(env):
        control = _Control(env)
        controls.append(control)
        return control

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: _container_cloud(),
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "basket",
            "pixel": [20, 30],
            "z_offset": 0.06,
            "hover": 0.02,
        },
    )

    assert result["released"] is True
    approach, final = controls[0].targets[:2]
    assert approach[2] >= final[2] + 0.04 - 1e-9


def test_explicit_pos_keeps_legacy_release_gap_when_perception_is_off(
    monkeypatch,
) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    controls = []

    def _control_factory(env):
        control = _Control(env)
        controls.append(control)
        return control

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "0")
    monkeypatch.setattr(policy, "LiberoControl", _control_factory)

    result, _ = policy.dispatch_runtime(
        state,
        {
            "pos": [0.15, 0.25, 0.80],
            "hover": 0.02,
        },
    )

    assert result["released"] is True
    approach, final = controls[0].targets[:2]
    assert final == pytest.approx([0.15, 0.25, 0.86])
    assert approach[2] >= final[2] + 0.04 - 1e-9


def test_relational_target_requires_explicit_pixel(monkeypatch) -> None:
    env = _Env()
    state = SimpleNamespace(env=env)
    localized = {"calls": 0}

    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", _Control)
    monkeypatch.setattr(perception, "_fix_on", lambda: True)
    monkeypatch.setattr(perception, "_place_fix_on", lambda: False)
    monkeypatch.setattr(
        policy,
        "_clear_localize",
        lambda *args: localized.__setitem__("calls", localized["calls"] + 1)
        or (20, 30),
    )

    result, _ = policy.dispatch_runtime(
        state,
        {
            "object": "back compartment of the caddy",
            "z_offset": 0.05,
        },
    )

    assert result["ok"] is False
    assert result["released"] is False
    assert "pixel" in result["reason"]
    assert localized["calls"] == 0
