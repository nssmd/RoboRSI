from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.find_by_pointing.libero.policy as pointing
import roborsi.embodied.skills.base.grasp_object.libero.policy as grasp
from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperState,
)
from roborsi.embodied.skills.base._lib.libero.semantic_point import (
    matching_semantic_point,
)


class _PointEnv:
    def __init__(self, frame: np.ndarray | None = None) -> None:
        self.frame = (
            np.zeros((32, 32, 3), dtype=np.uint8)
            if frame is None
            else frame.copy()
        )

    def take_snapshot(self):
        return SimpleNamespace(
            images={"head_camera": self.frame.copy()},
            state=np.zeros(1, dtype=np.float32),
        )

    def depth_map(self, camera="agentview"):
        _ = camera
        return np.full((32, 32), 0.42, dtype=np.float32)


def _point(monkeypatch, env: _PointEnv, name: str = "black bowl in the middle"):
    monkeypatch.setattr(
        perception,
        "localize_precise",
        lambda state, obj, route: (16, 16),
    )
    return pointing.dispatch_runtime(
        SimpleNamespace(env=env),
        {"object": name},
    )[0]


def test_pointing_records_current_frame_provenance(monkeypatch) -> None:
    env = _PointEnv()

    result = _point(monkeypatch, env)

    evidence = getattr(env, "_libero_semantic_point_evidence", None)
    assert result["ok"] is True
    assert evidence.object_name == "black bowl in the middle"
    assert evidence.pixel == (16, 16)
    assert evidence.source == "vlm->sam"
    assert np.array_equal(evidence.frame, env.frame)


def test_pointing_provenance_rejects_changed_frame_or_object(monkeypatch) -> None:
    env = _PointEnv()
    _point(monkeypatch, env)

    assert matching_semantic_point(
        env,
        object_name="black bowl at the front",
        pixel=(16, 16),
        current_frame=env.frame,
    ) is None
    changed = env.frame.copy()
    changed[:] = 20
    assert matching_semantic_point(
        env,
        object_name="black bowl in the middle",
        pixel=(16, 16),
        current_frame=changed,
    ) is None


def test_grasp_reuses_matching_pointing_without_second_identity_call(
    monkeypatch,
) -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    env = _PointEnv(before)
    assert _point(monkeypatch, env)["ok"] is True

    class _Control:
        def __init__(self, wrapped):
            self.env = wrapped
            self.reads = 0

        def read_gripper_state(self):
            self.reads += 1
            if self.reads == 1:
                return 0.08, GripperState.OPEN
            return 0.03, GripperState.HELD

    monkeypatch.setattr(grasp, "LiberoControl", _Control)
    monkeypatch.setattr(grasp, "_locate_pixel", lambda *args: (16, 16))
    monkeypatch.setattr(grasp, "_fix_on", lambda: False)
    monkeypatch.setattr(grasp, "_clear_source_view", lambda *args: (True, None))
    monkeypatch.setattr(
        perception,
        "_requires_semantic_pointing",
        lambda name: True,
    )
    monkeypatch.setattr(
        grasp,
        "_verify_object_pixel",
        lambda *args, **kwargs: pytest.fail(
            "matching same-frame semantic point must not be re-asked"
        ),
    )
    monkeypatch.setattr(
        perception,
        "grasps_at_pixel",
        lambda *args, **kwargs: ([object()], None),
    )

    def _execute(*args, **kwargs):
        _ = (args, kwargs)
        env.frame = after.copy()
        return (
            np.array([0.0, 0.0, 0.5]),
            np.array([0.0, 0.0, 0.8]),
            np.zeros(2),
        )

    monkeypatch.setattr(perception, "execute_topdown", _execute)

    result, _ = grasp._perception_grasp(
        SimpleNamespace(env=env),
        {
            "object": "black bowl in the middle",
            "pixel": [16, 16],
        },
    )

    assert result["grasped"] is True
    assert result["identity_verified"] is True
