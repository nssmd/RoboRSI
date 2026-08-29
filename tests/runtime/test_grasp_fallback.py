from __future__ import annotations

import numpy as np

from roborsi.embodied.skills.base._lib.libero._perception import (
    geometric_grasp_candidates,
)


def test_geometry_fallback_returns_bounded_topdown_candidate() -> None:
    cloud = np.array(
        [
            [0.10, 0.20, 0.74],
            [0.12, 0.20, 0.76],
            [0.10, 0.22, 0.75],
            [0.12, 0.22, 0.77],
        ]
        * 10,
        dtype=np.float32,
    )

    candidates = geometric_grasp_candidates(cloud)

    assert len(candidates) == 1
    point = np.asarray(candidates[0]["translation_tcp_world"])
    assert 0.10 <= point[0] <= 0.12
    assert 0.20 <= point[1] <= 0.22
    assert 0.74 <= point[2] <= 0.77
    assert candidates[0]["source"] == "geometric_topdown_fallback"
