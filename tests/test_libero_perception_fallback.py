from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from roborsi.embodied.skills.base._lib.libero import _perception
from roborsi.embodied.skills.base.find_pixel.libero import policy as find_pixel
from roborsi.embodied.skills.base.grasp_object.libero import policy as grasp_object


def _state() -> SimpleNamespace:
    observation = SimpleNamespace(
        images={"head_camera": np.zeros((16, 16, 3), dtype=np.uint8)}
    )
    env = SimpleNamespace(take_snapshot=lambda: observation)
    return SimpleNamespace(env=env)


def test_find_pixel_uses_perception_model_before_local_detector(monkeypatch) -> None:
    monkeypatch.setattr(_perception, "vlm_point", lambda *_args: (7, 9))

    result, _ = find_pixel.dispatch_runtime(
        _state(),
        {"object": "alphabet soup", "location": "center"},
    )

    assert result["ok"] is True
    assert (result["u"], result["v"]) == (7, 9)
    assert result["bbox"] is None


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
