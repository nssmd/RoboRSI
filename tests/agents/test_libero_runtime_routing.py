from __future__ import annotations

import os

import numpy as np

from roborsi.agents.engineer import _configure_perception_backend
from roborsi.agents.planner import _libero_runtime_routing_block
from roborsi.embodied.skills.base._lib.libero._perception import (
    _project_world_to_head_pixel,
    _requires_orbit_product_identity,
    _select_orbit_consensus,
)


def test_libero_runtime_routing_overrides_cross_task_memory() -> None:
    block = _libero_runtime_routing_block("libero")

    assert "runtime task instruction is the authority" in block
    assert "place_object_in" in block
    assert "place_on_surface" in block
    assert _libero_runtime_routing_block("robotwin") == ""


def test_libero_perception_follows_explicit_openai_role_model(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ROBORSI_PERCEPTION_MODEL", raising=False)
    monkeypatch.delenv("ROBORSI_VLM_PROVIDER", raising=False)

    _configure_perception_backend("libero", "gpt-5.6-sol")

    assert os.environ["ROBORSI_PERCEPTION_MODEL"] == "gpt-5.6-sol"
    assert os.environ["ROBORSI_VLM_PROVIDER"] == "openai"


def test_orbit_identity_is_reserved_for_exact_products() -> None:
    assert _requires_orbit_product_identity("alphabet soup")
    assert _requires_orbit_product_identity("cream cheese box")
    assert not _requires_orbit_product_identity("yellow plate")
    assert not _requires_orbit_product_identity("black bowl between two plates")


def test_orbit_consensus_rejects_one_view_outlier() -> None:
    rows = [
        {"world": [-0.15, -0.24, 0.05], "confidence": 0.91},
        {"world": [-0.12, -0.27, 0.06], "confidence": 0.90},
        {"world": [-0.12, -0.21, 0.05], "confidence": 0.91},
        {"world": [0.18, 0.03, 0.06], "confidence": 0.96},
    ]

    point = _select_orbit_consensus(rows)

    assert point is not None
    assert np.linalg.norm(point - np.array([-0.12, -0.24, 0.05])) < 0.03


def test_world_point_projects_back_to_head_image() -> None:
    class Env:
        def camera_matrices(self, camera: str):
            assert camera == "agentview"
            return (
                np.array(
                    [
                        [100.0, 0.0, 50.0],
                        [0.0, 100.0, 50.0],
                        [0.0, 0.0, 1.0],
                    ]
                ),
                np.eye(4),
            )

        def take_snapshot(self):
            return type(
                "Observation",
                (),
                {"images": {"head_camera": np.zeros((100, 100, 3))}},
            )()

    assert _project_world_to_head_pixel(Env(), [0.1, 0.2, 1.0]) == (60, 70)
