from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.place_object_in.libero.policy as policy
from roborsi.embodied.skills.base._lib.libero.drawer_evidence import (
    DrawerPullEvidence,
    resolve_open_drawer_point,
)
from roborsi.embodied.skills.base._lib.libero.gripper_state import GripperState


def _basket_cloud(
    *,
    center=(0.0, 0.25),
    half_extent=(0.14, 0.055),
    floor_z=0.76,
    rim_z=0.82,
) -> np.ndarray:
    cx, cy = center
    hx, hy = half_extent
    xs = np.linspace(cx - hx, cx + hx, 11)
    ys = np.linspace(cy - hy, cy + hy, 9)
    zs = np.linspace(floor_z, rim_z, 4)
    rows = []
    for z in zs:
        rows.extend((x, cy - hy, z) for x in xs)
        rows.extend((x, cy + hy, z) for x in xs)
        rows.extend((cx - hx, y, z) for y in ys)
        rows.extend((cx + hx, y, z) for y in ys)
    rows.extend((x, y, floor_z) for x in xs[1:-1] for y in ys[1:-1])
    return np.asarray(rows, dtype=float)


def _object_cloud(
    *,
    center=(0.0, 0.25),
    half_extent=(0.018, 0.012),
    z_low=0.775,
    z_high=0.835,
) -> np.ndarray:
    cx, cy = center
    hx, hy = half_extent
    xs = np.linspace(cx - hx, cx + hx, 5)
    ys = np.linspace(cy - hy, cy + hy, 4)
    zs = np.linspace(z_low, z_high, 4)
    return np.asarray(
        [(x, y, z) for x in xs for y in ys for z in zs],
        dtype=float,
    )


class _Env:
    def __init__(self, pixel_world=(0.0, 0.25, 0.80)) -> None:
        self.pixel_world = np.asarray(pixel_world, dtype=float)

    def take_snapshot(self):
        return SimpleNamespace(
            images={
                "head_camera": np.zeros((64, 64, 3), dtype=np.uint8),
            }
        )

    def pixel_to_world(self, u, v, camera="agentview"):
        return self.pixel_world.copy()

    def raw_obs(self):
        raise AssertionError("container placement must not read simulator state")

    def check_success(self):
        raise AssertionError("container placement must not read success predicates")


class _Control:
    def __init__(self, env, quat=(0.0, 0.0, 0.0, 1.0)) -> None:
        self.env = env
        self.quat = np.asarray(quat, dtype=float)
        self.pose = np.array([0.0, 0.0, 1.0], dtype=float)
        self.opened = False
        self.targets = []
        self.gripper_calls = 0

    def read_pose(self):
        return self.pose.copy(), self.quat.copy(), np.zeros(2)

    def read_gripper_state(self):
        if self.opened:
            return 0.08, GripperState.OPEN
        return 0.02, GripperState.HELD

    def servo_to(self, pos, **kwargs):
        target = np.asarray(pos, dtype=float)
        self.targets.append((target, kwargs))
        self.pose = target.copy()
        return True, None

    def servo_correction_to(self, pos, **kwargs):
        return self.servo_to(pos, **kwargs)

    def set_gripper(self, *, close, steps=12):
        self.gripper_calls += 1
        self.opened = not close


def _run(
    monkeypatch,
    *,
    container_cloud,
    released_cloud,
    pixel_world=(0.0, 0.25, 0.80),
    object_offset_local=None,
    post_pixel=(30, 30),
    post_identity_verified=True,
    quat=(0.0, 0.0, 0.0, 1.0),
):
    env = _Env(pixel_world)
    control = _Control(env, quat=quat)
    clouds = iter([container_cloud, released_cloud])
    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", lambda _env: control)
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            reason="source_patch_remains_cleared",
            object_name="alphabet soup can",
            identity_verified=True,
        ),
    )
    monkeypatch.setattr(
        policy,
        "get_visual_hold",
        lambda _env: SimpleNamespace(
            object_name="alphabet soup can",
            identity_verified=True,
            object_offset_local=object_offset_local,
        ),
    )
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: next(clouds),
    )
    monkeypatch.setattr(
        perception,
        "localize_precise",
        lambda state, name: post_pixel,
    )
    monkeypatch.setattr(
        policy,
        "_verify_released_object_pixel",
        lambda *args, **kwargs: post_identity_verified,
        raising=False,
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "wicker basket",
            "pixel": [20, 20],
            "z_offset": 0.04,
            "hover": 0.12,
        },
    )
    return result, control


def test_container_place_applies_visual_hold_offset_before_release(
    monkeypatch,
) -> None:
    result, control = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        object_offset_local=(0.03, -0.04, -0.01),
    )

    final_target = control.targets[1][0]
    assert final_target[:2] == pytest.approx([-0.03, 0.29], abs=1e-4)
    assert result["held_object_offset_world"] == pytest.approx(
        [0.03, -0.04, -0.01],
    )
    assert result["released"] is True
    assert result["placed"] is True
    assert result["ok"] is True


def test_container_place_rotates_local_offset_into_world_frame(
    monkeypatch,
) -> None:
    result, control = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        object_offset_local=(0.03, 0.0, 0.0),
        quat=(0.0, 0.0, 1.0, 0.0),
    )

    final_target = control.targets[1][0]
    assert final_target[:2] == pytest.approx([0.03, 0.25], abs=1e-4)
    assert result["held_object_offset_world"] == pytest.approx(
        [-0.03, 0.0, 0.0],
        abs=1e-4,
    )


def test_container_place_rejects_untrusted_visual_hold_offset(
    monkeypatch,
) -> None:
    result, control = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        object_offset_local=(0.25, 0.0, 0.0),
    )

    assert result["ok"] is False
    assert result["released"] is False
    assert result["placed"] is False
    assert "offset" in result["reason"]
    assert control.gripper_calls == 0


def test_supported_rim_pixel_uses_container_footprint_not_centroid_distance(
    monkeypatch,
) -> None:
    result, _ = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        pixel_world=(0.138, 0.25, 0.82),
    )

    assert result["released"] is True
    assert result["placed"] is True
    assert result["container_pixel_supported"] is True


def test_release_without_post_release_localization_is_not_placed(
    monkeypatch,
) -> None:
    result, _ = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        post_pixel=None,
    )

    assert result["released"] is True
    assert result["placed"] is False
    assert result["ok"] is False
    assert result["post_release_visual_containment"]["verified"] is False
    assert (
        result["post_release_visual_containment"]["reason"]
        == "released object could not be localized after release"
    )


def test_post_release_identity_mismatch_is_not_placed(
    monkeypatch,
) -> None:
    result, _ = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(),
        post_identity_verified=False,
    )

    assert result["released"] is True
    assert result["placed"] is False
    assert result["ok"] is False
    assert (
        result["post_release_visual_containment"]["reason"]
        == "post-release object identity was not visually verified"
    )


def test_object_resting_on_rim_is_released_but_not_placed(
    monkeypatch,
) -> None:
    result, _ = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(z_low=0.824, z_high=0.87),
    )

    assert result["released"] is True
    assert result["placed"] is False
    assert result["ok"] is False
    evidence = result["post_release_visual_containment"]
    assert evidence["inside_xy"] is True
    assert evidence["below_rim"] is False


def test_object_outside_container_is_released_but_not_placed(
    monkeypatch,
) -> None:
    result, _ = _run(
        monkeypatch,
        container_cloud=_basket_cloud(),
        released_cloud=_object_cloud(center=(0.22, 0.25)),
    )

    assert result["released"] is True
    assert result["placed"] is False
    assert result["ok"] is False
    assert result["post_release_visual_containment"]["inside_xy"] is False


def test_container_mask_cannot_verify_itself_as_released_object(
    monkeypatch,
) -> None:
    basket = _basket_cloud()
    result, _ = _run(
        monkeypatch,
        container_cloud=basket,
        released_cloud=basket.copy(),
    )

    assert result["released"] is True
    assert result["placed"] is False
    assert result["ok"] is False
    assert (
        result["post_release_visual_containment"]["reason"]
        == "post-release object cloud is not distinct from container"
    )


def test_drawer_place_centers_translated_surface_inside_pull_corridor(
    monkeypatch,
) -> None:
    pixel_world = np.array([0.0329, -0.2454, 1.1280], dtype=float)
    handle_before = np.array([0.0311, -0.1342, 1.0934], dtype=float)
    pull_normal = np.array([0.0540, 0.9985, 0.0], dtype=float)
    pull_normal /= np.linalg.norm(pull_normal)
    achieved = 0.1596
    translated_surface = pixel_world + pull_normal * achieved
    expected_drawer_point = translated_surface.copy()
    source_longitudinal = float(
        np.dot(
            (pixel_world - handle_before)[:2],
            pull_normal[:2],
        )
    )
    expected_longitudinal = 0.5 * (
        source_longitudinal + achieved
    )
    expected_drawer_point[:2] = (
        handle_before[:2]
        + expected_longitudinal * pull_normal[:2]
    )

    env = _Env(pixel_world=pixel_world)
    control = _Control(env)
    monkeypatch.setenv("ROBORSI_LIBERO_PERCEPTION", "1")
    monkeypatch.setattr(policy, "LiberoControl", lambda _env: control)
    monkeypatch.setattr(
        policy,
        "verify_visual_hold",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            reason="source_patch_remains_cleared",
            object_name="bowl",
            identity_verified=True,
        ),
    )
    monkeypatch.setattr(
        policy,
        "get_visual_hold",
        lambda _env: SimpleNamespace(
            object_name="bowl",
            identity_verified=True,
            object_offset_local=None,
        ),
    )
    monkeypatch.setattr(
        policy,
        "get_drawer_pull_evidence",
        lambda _env: SimpleNamespace(
            target_name="top drawer handle",
            layer="top",
            handle_point_before=tuple(handle_before),
            face_normal=tuple(pull_normal),
            achieved_pull_distance=achieved,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        perception,
        "object_cloud",
        lambda *args, **kwargs: None,
    )

    result, _ = policy.dispatch_runtime(
        SimpleNamespace(env=env),
        {
            "object": "inside the open top layer of the drawer",
            "pixel": [32, 121],
            "z_offset": 0.03,
            "hover": 0.12,
        },
    )

    assert result["released"] is True
    assert result["pixel_fallback"] == "drawer_pull_evidence"
    assert control.targets[0][0] == pytest.approx(
        expected_drawer_point + np.array([0.0, 0.0, 0.07]),
        abs=1e-4,
    )
    assert control.targets[1][0] == pytest.approx(
        expected_drawer_point + np.array([0.0, 0.0, 0.03]),
        abs=1e-4,
    )


def test_drawer_resolution_projects_visual_pixel_to_handle_centerline() -> None:
    handle = np.array([0.0333, -0.1464, 1.0986], dtype=float)
    normal = np.array([0.0005, 1.0, 0.0], dtype=float)
    normal /= np.linalg.norm(normal)
    lateral_axis = np.array([-normal[1], normal[0], 0.0], dtype=float)
    distance = 0.1588
    source_longitudinal = -0.0599
    pixel = (
        handle
        + normal * source_longitudinal
        + lateral_axis * 0.1142
    )
    pixel[2] = 1.128
    evidence = DrawerPullEvidence(
        target_name="top drawer handle",
        layer="top",
        handle_point_before=tuple(handle),
        face_normal=tuple(normal),
        achieved_pull_distance=distance,
    )

    resolution, reason = resolve_open_drawer_point(
        evidence,
        target_name="inside the open top layer of the drawer",
        pixel_world=pixel,
    )

    assert reason is None
    assert resolution is not None
    resolved = np.asarray(resolution.point, dtype=float)
    delta = resolved - handle
    expected_longitudinal = 0.5 * (
        source_longitudinal + distance
    )
    assert float(np.dot(delta[:2], normal[:2])) == pytest.approx(
        expected_longitudinal
    )
    assert float(np.dot(delta, lateral_axis)) == pytest.approx(0.0, abs=1e-9)
