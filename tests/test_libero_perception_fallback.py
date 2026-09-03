from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from roborsi.embodied.skills.base._lib.libero import _perception
from roborsi.embodied.skills.base._lib.libero._helpers import classify_gripper_gap
from roborsi.embodied.skills.base.find_pixel.libero import policy as find_pixel
from roborsi.embodied.skills.base.grasp_object.libero import policy as grasp_object
from roborsi.embodied.skills.base.place_object_in.libero import (
    policy as place_object_in,
)


def _state() -> SimpleNamespace:
    observation = SimpleNamespace(
        images={"head_camera": np.zeros((16, 16, 3), dtype=np.uint8)}
    )
    env = SimpleNamespace(take_snapshot=lambda: observation)
    return SimpleNamespace(env=env)


def test_find_pixel_uses_perception_model_before_local_detector(monkeypatch) -> None:
    state = _state()
    monkeypatch.setattr(_perception, "vlm_point", lambda *_args: (7, 9))

    result, _ = find_pixel.dispatch_runtime(
        state,
        {"object": "alphabet soup", "location": "center"},
    )

    assert result["ok"] is True
    assert (result["u"], result["v"]) == (7, 9)
    assert result["bbox"] is None
    assert _perception.recall_pixel(state, "alphabet soup can") == (7, 9)


def test_grasp_prefers_explicit_visual_pixel(monkeypatch) -> None:
    monkeypatch.setattr(
        _perception,
        "localize_precise",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not relocalize")),
    )

    assert grasp_object._locate_pixel(
        _state(),
        {"object": "alphabet soup", "pixel": [11, 13]},
    ) == (11, 13)


def test_grasp_reuses_cached_visual_pixel(monkeypatch) -> None:
    state = _state()
    _perception.remember_pixel(state, "alphabet soup container", (17, 19))
    monkeypatch.setattr(
        _perception,
        "localize_precise",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not relocalize")),
    )

    assert grasp_object._locate_pixel(
        state,
        {"object": "alphabet soup container at the right side"},
    ) == (17, 19)


def test_unprojected_target_and_object_height_define_drop_point() -> None:
    state = _state()
    _perception.remember_pixel(state, "basket", (20, 21))
    _perception.remember_world(state, 20, 21, (-0.04, 0.27, 0.09))
    state._held_object_height = 0.12

    drop = place_object_in._drop_from_memory(state, "basket", 0.03)

    assert np.allclose(drop, [-0.04, 0.27, 0.18])


def test_gripper_gap_distinguishes_open_holding_and_closed() -> None:
    assert classify_gripper_gap(0.001) == "closed_empty"
    assert classify_gripper_gap(0.0588) == "holding"
    assert classify_gripper_gap(0.0799) == "open"


def test_segmented_cloud_has_topdown_fallback_without_graspgen(
    monkeypatch,
) -> None:
    cloud = np.asarray([
        [0.30, 0.10, 0.80],
        [0.32, 0.12, 0.82],
        [0.34, 0.14, 0.84],
    ], dtype=np.float32)
    monkeypatch.delenv("GRASPGEN_PORT", raising=False)
    monkeypatch.setattr(_perception, "object_cloud", lambda *_args: cloud)

    grasps, returned_cloud = _perception.grasps_at_pixel(
        SimpleNamespace(),
        10,
        12,
    )

    assert returned_cloud is cloud
    assert grasps[0]["source"] == "sam+depth-topdown"
    assert np.allclose(
        grasps[0]["translation_tcp_world"],
        np.median(cloud, axis=0),
    )
