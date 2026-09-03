from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from roborsi.embodied.sim.libero.adapter import LiberoProEnv
from roborsi.embodied.sim.libero.orbit_geometry import (
    OrbitFrame,
    compose_orbit_sheet,
    triangulate_rays,
)
from roborsi.embodied.skills.base.execute_previewed_move.libero import (
    policy as execute_policy,
)
from roborsi.embodied.skills.base.mark_orbit_point.libero import (
    policy as mark_policy,
)
from roborsi.embodied.skills.base.observe_orbit.libero import (
    policy as observe_policy,
)
from roborsi.embodied.skills.base.preview_move_to_pose.libero import (
    policy as preview_policy,
)


def _frame(name: str = "orbit_front") -> OrbitFrame:
    return OrbitFrame(
        name=name,
        rgb=np.zeros((4, 4, 3), dtype=np.uint8),
        depth_m=np.full((4, 4), 2.0, dtype=np.float64),
        camera_position_world=np.asarray([1.0, 2.0, 3.0]),
        camera_to_world_rotation=np.eye(3),
        fx=2.0,
        fy=2.0,
        cx=1.0,
        cy=1.0,
    )


def test_orbit_frame_backprojects_visible_surface() -> None:
    frame = _frame()

    assert frame.world_at(1, 1) == [1.0, 2.0, 5.0]


def test_orbit_frame_rejects_invalid_pixel_or_depth() -> None:
    frame = _frame()
    frame.depth_m[1, 1] = np.nan

    assert frame.world_at(1, 1) is None
    assert frame.world_at(-1, 0) is None
    assert frame.world_at(4, 0) is None


def test_orbit_frame_exposes_calibrated_camera_ray() -> None:
    origin, direction = _frame().ray_at(1, 1)

    np.testing.assert_allclose(origin, [1.0, 2.0, 3.0])
    np.testing.assert_allclose(direction, [0.0, 0.0, 1.0])


def test_triangulate_rays_returns_midpoint_and_separation() -> None:
    result = triangulate_rays(
        np.asarray([0.0, 0.0, 0.0]),
        np.asarray([0.0, 0.0, 1.0]),
        np.asarray([1.0, 0.0, 1.0]),
        np.asarray([-1.0, 0.0, 0.0]),
    )

    assert result is not None
    point, separation = result
    np.testing.assert_allclose(point, [0.0, 0.0, 1.0], atol=1e-8)
    assert separation == 0.0


def test_compose_orbit_sheet_has_stable_layout() -> None:
    sheet = compose_orbit_sheet(
        [_frame("orbit_front"), _frame("orbit_left"), _frame("orbit_top")],
        tile_size=32,
    )

    assert sheet.dtype == np.uint8
    assert sheet.shape == (64, 64, 3)


def test_observe_orbit_attaches_requested_view(tmp_path) -> None:
    frame = _frame()

    class _Env:
        def capture_orbit_views(self, *, image_size: int):
            assert image_size == 512
            return {frame.name: frame}

        def take_snapshot(self):
            return SimpleNamespace(images={})

    state = SimpleNamespace(env=_Env(), workdir=tmp_path, last_image_path=None)

    result, _ = observe_policy.dispatch_runtime(
        state,
        {"view": frame.name, "image_size": 512},
    )

    assert result["ok"] is True
    assert result["views"] == [frame.name]
    assert state.last_image_path.is_file()


def test_mark_orbit_point_returns_depth_backed_world_point(tmp_path) -> None:
    frame = _frame()

    class _Env:
        def orbit_frame(self, view: str):
            assert view == frame.name
            return frame

        def orbit_pixel_to_world(self, view: str, u: int, v: int):
            assert (view, u, v) == (frame.name, 1, 1)
            return frame.world_at(u, v)

        def take_snapshot(self):
            return SimpleNamespace(images={})

    state = SimpleNamespace(env=_Env(), workdir=tmp_path, last_image_path=None)

    result, _ = mark_policy.dispatch_runtime(
        state,
        {"view": frame.name, "u": 1, "v": 1},
    )

    assert result == {
        "ok": True,
        "view": frame.name,
        "pixel": [1, 1],
        "world": [1.0, 2.0, 5.0],
        "source": "orbit_rgbd_surface",
    }
    assert state.last_image_path.is_file()


def test_mark_orbit_point_solves_free_space_from_two_view_rays(tmp_path) -> None:
    first = OrbitFrame(
        name="orbit_front",
        rgb=np.zeros((4, 4, 3), dtype=np.uint8),
        depth_m=np.full((4, 4), 2.0, dtype=np.float64),
        camera_position_world=np.asarray([0.0, 0.0, 0.0]),
        camera_to_world_rotation=np.eye(3),
        fx=2.0,
        fy=2.0,
        cx=1.0,
        cy=1.0,
    )
    second = OrbitFrame(
        name="orbit_side",
        rgb=np.zeros((4, 4, 3), dtype=np.uint8),
        depth_m=np.full((4, 4), 2.0, dtype=np.float64),
        camera_position_world=np.asarray([1.0, 0.0, 1.0]),
        camera_to_world_rotation=np.asarray(
            [[0.0, 0.0, -1.0], [1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]
        ),
        fx=2.0,
        fy=2.0,
        cx=1.0,
        cy=1.0,
    )
    frames = {first.name: first, second.name: second}

    class _Env:
        def orbit_frame(self, view: str):
            return frames.get(view)

        def orbit_generation(self) -> int:
            return 3

        def take_snapshot(self):
            return SimpleNamespace(images={})

    state = SimpleNamespace(env=_Env(), workdir=tmp_path, last_image_path=None)
    pending, _ = mark_policy.dispatch_runtime(
        state,
        {"mode": "ray", "view": first.name, "u": 1, "v": 1},
    )

    assert pending["ok"] is True
    assert pending["complete"] is False
    point_id = pending["point_id"]

    solved, _ = mark_policy.dispatch_runtime(
        state,
        {
            "mode": "ray",
            "point_id": point_id,
            "view": second.name,
            "u": 1,
            "v": 1,
        },
    )

    assert solved["ok"] is True
    assert solved["complete"] is True
    np.testing.assert_allclose(solved["world"], [0.0, 0.0, 1.0], atol=1e-8)
    assert solved["source"] == "orbit_two_ray_triangulation"


def test_libero_adapter_captures_orbit_views_without_changing_camera_state() -> None:
    class _Camera:
        type = 2
        fixedcamid = 4
        lookat = np.asarray([9.0, 8.0, 7.0])
        distance = 3.0
        azimuth = 17.0
        elevation = -11.0

    class _Eye:
        frustum_near = 0.1
        frustum_far = 10.0
        frustum_top = 0.1
        pos = np.asarray([0.0, 0.0, 0.0])
        forward = np.asarray([0.0, 0.0, 1.0])
        up = np.asarray([0.0, -1.0, 0.0])

    class _Context:
        def __init__(self) -> None:
            self.cam = _Camera()
            self.scn = SimpleNamespace(camera=[_Eye(), _Eye()])
            self.render_calls = []

        def render(self, width: int, height: int, camera_id: int) -> None:
            self.render_calls.append((width, height, camera_id))

        def read_pixels(self, width: int, height: int, depth: bool):
            assert depth is True
            return (
                np.zeros((height, width, 3), dtype=np.uint8),
                np.full((height, width), 0.5, dtype=np.float64),
            )

    context = _Context()
    adapter = object.__new__(LiberoProEnv)
    adapter._env = SimpleNamespace(
        env=SimpleNamespace(
            sim=SimpleNamespace(_render_context_offscreen=context)
        )
    )
    adapter._orbit_frames = {}
    adapter._bind_gl_context = lambda: None
    adapter._vision_workspace_center = lambda: np.asarray([0.1, 0.2, 0.3])

    frames = adapter.capture_orbit_views(image_size=64)

    assert len(frames) == 5
    assert len(context.render_calls) == 5
    assert all(frame.rgb.shape == (64, 64, 3) for frame in frames.values())
    assert context.cam.type == 2
    assert context.cam.fixedcamid == 4
    np.testing.assert_allclose(context.cam.lookat, [9.0, 8.0, 7.0])
    assert context.cam.distance == 3.0
    assert context.cam.azimuth == 17.0
    assert context.cam.elevation == -11.0
    assert adapter.orbit_frame("orbit_front") is frames["orbit_front"]
    assert adapter.orbit_pixel_to_world("orbit_front", 32, 32) is not None


def test_vision_workspace_center_accepts_single_channel_depth() -> None:
    adapter = object.__new__(LiberoProEnv)
    adapter.depth_map = lambda camera: np.ones((4, 4, 1), dtype=np.float64)
    adapter.camera_matrices = lambda camera: (np.eye(3), np.eye(4))

    center = adapter._vision_workspace_center()

    assert center.shape == (3,)
    assert np.all(np.isfinite(center))


def test_preview_move_to_pose_stores_one_time_ik_checked_token(
    tmp_path,
    monkeypatch,
) -> None:
    frame = _frame()

    class _Control:
        def __init__(self, env) -> None:
            self.env = env

        def read_pose(self):
            return np.zeros(3), np.asarray([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

        def preview_goal_config(self, pos, quat):
            assert np.allclose(pos, [0.0, 0.0, 1.0])
            return np.zeros(7)

        def preview_trajectory(self, pos, quat):
            assert np.allclose(pos, [0.0, 0.0, 1.0])
            return np.vstack([np.zeros(7), np.full(7, 0.1)])

    class _Env:
        def capture_orbit_views(self, *, image_size: int):
            return {frame.name: frame}

        def orbit_generation(self) -> int:
            return 8

        def take_snapshot(self):
            return SimpleNamespace(images={})

    monkeypatch.setattr(preview_policy, "LiberoControl", _Control)
    state = SimpleNamespace(env=_Env(), workdir=tmp_path, last_image_path=None)

    result, _ = preview_policy.dispatch_runtime(
        state,
        {"pos": [0.0, 0.0, 1.0], "gripper": "close"},
    )

    assert result["ok"] is True
    assert result["reachable"] is True
    assert result["preview_id"] == "move-preview-0001"
    assert state.last_image_path.is_file()
    assert result["preview_id"] in state._libero_move_previews
    assert (
        state._libero_move_previews[result["preview_id"]]["args"]["via_trajopt"]
        is True
    )
    assert state._libero_move_previews[result["preview_id"]]["trajectory"] == [
        [0.0] * 7,
        [0.1] * 7,
    ]
    assert result["trajectory_waypoints"] == 2


def test_preview_move_to_pose_rejects_endpoint_without_trajectory(
    tmp_path,
    monkeypatch,
) -> None:
    class _Control:
        def __init__(self, env) -> None:
            self.env = env

        def read_pose(self):
            return np.zeros(3), np.asarray([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

        def preview_goal_config(self, pos, quat):
            return np.zeros(7)

        def preview_trajectory(self, pos, quat):
            return None

    class _Env:
        def take_snapshot(self):
            return SimpleNamespace(images={})

    monkeypatch.setattr(preview_policy, "LiberoControl", _Control)
    state = SimpleNamespace(env=_Env(), workdir=tmp_path, last_image_path=None)

    result, _ = preview_policy.dispatch_runtime(
        state,
        {"pos": [0.0, 0.0, 1.0], "gripper": "close"},
    )

    assert result["ok"] is True
    assert result["reachable"] is False
    assert "trajectory" in result["reason"]
    assert not hasattr(state, "_libero_move_previews")


def test_execute_previewed_move_consumes_token_and_attaches_fresh_view(
    tmp_path,
    monkeypatch,
) -> None:
    class _Control:
        def __init__(self, env) -> None:
            self.env = env

        def read_pose(self):
            return np.zeros(3), np.asarray([0.0, 0.0, 0.0, 1.0]), np.zeros(2)

    class _Env:
        def orbit_generation(self) -> int:
            return 8

        def take_snapshot(self):
            return SimpleNamespace(images={})

    calls = []

    def _execute(state, args):
        calls.append(args)
        return {"ok": True, "reached": True}, state.env.take_snapshot()

    monkeypatch.setattr(execute_policy, "LiberoControl", _Control)
    monkeypatch.setattr(execute_policy, "_execute_move", _execute)
    monkeypatch.setattr(execute_policy, "_attach_fresh_head", lambda state: None)
    state = SimpleNamespace(
        env=_Env(),
        workdir=tmp_path,
        last_image_path=None,
        _libero_move_previews={
            "move-preview-0001": {
                "args": {
                    "pos": [0.0, 0.0, 1.0],
                    "gripper": "close",
                    "via_trajopt": True,
                },
                "generation": 8,
                "ee_pos": [0.0, 0.0, 0.0],
                "trajectory": [[0.0] * 7, [0.1] * 7],
            }
        },
    )

    result, _ = execute_policy.dispatch_runtime(
        state,
        {"preview_id": "move-preview-0001"},
    )

    assert result["ok"] is True
    assert result["preview_id"] == "move-preview-0001"
    assert calls == [
        {
            "pos": [0.0, 0.0, 1.0],
            "gripper": "close",
            "via_trajopt": True,
            "_planned_trajectory": [[0.0] * 7, [0.1] * 7],
        }
    ]
    assert "move-preview-0001" not in state._libero_move_previews

    replay, _ = execute_policy.dispatch_runtime(
        state,
        {"preview_id": "move-preview-0001"},
    )
    assert replay["ok"] is False
