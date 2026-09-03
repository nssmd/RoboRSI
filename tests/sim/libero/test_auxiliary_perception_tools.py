from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import roborsi.embodied.skills.base._lib.libero._perception as perception
import roborsi.embodied.skills.base.find_by_detector.libero.policy as detector_policy
import roborsi.embodied.skills.base.find_by_pointing.libero.policy as pointing_policy
import roborsi.embodied.skills.base.find_pixel.libero.policy as find_pixel_policy
import roborsi.embodied.skills.base.get_grasp_pose.libero.policy as grasp_policy
import roborsi.embodied.skills.base.is_reachable.libero.policy as reach_policy


class _Env:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(
            images={"head_camera": np.zeros((32, 32, 3), dtype=np.uint8)},
        )

    def take_snapshot(self):
        return self.snapshot

    def robot_base_pos(self):
        return np.zeros(3, dtype=float)


@pytest.mark.parametrize(
    "pixel",
    (
        [np.nan, 12],
        [-1, 12],
        [-0.1, 12],
        [12.5, 12],
        [True, 12],
        [32, 12],
        [12, 32],
        ["bad", 12],
    ),
)
def test_get_grasp_pose_rejects_invalid_pixel(monkeypatch, pixel) -> None:
    monkeypatch.setattr(
        grasp_policy,
        "grasps_at_pixel",
        lambda *args, **kwargs: pytest.fail("invalid pixel must not dispatch"),
    )

    result, _ = grasp_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"pixel": pixel},
    )

    assert result["ok"] is False


def test_get_grasp_pose_returns_executable_quaternion(monkeypatch) -> None:
    monkeypatch.setattr(
        grasp_policy,
        "grasps_at_pixel",
        lambda env, u, v, top_k: (
            [
                {
                    "score": 0.9,
                    "translation_tcp_world": np.array([0.1, 0.2, 0.3]),
                    "rotation_matrix_world": np.eye(3),
                    "approach_z": 1.0,
                }
            ],
            np.zeros((20, 3)),
        ),
    )

    result, _ = grasp_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"pixel": [12, 12]},
    )

    assert result["ok"] is True
    assert len(result["grasps"]) == 1
    quat = np.asarray(result["grasps"][0]["quat"], dtype=float)
    assert quat.shape == (4,)
    assert np.all(np.isfinite(quat))


def test_get_grasp_pose_rejects_nonfinite_score(monkeypatch) -> None:
    monkeypatch.setattr(
        grasp_policy,
        "grasps_at_pixel",
        lambda env, u, v, top_k: (
            [
                {
                    "score": np.nan,
                    "translation_tcp_world": np.array([0.1, 0.2, 0.3]),
                    "rotation_matrix_world": np.eye(3),
                    "approach_z": 1.0,
                }
            ],
            np.zeros((20, 3)),
        ),
    )

    result, _ = grasp_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"pixel": [12, 12]},
    )

    assert result["ok"] is False
    assert result["reason"] == "grasp candidates lacked finite 6-DoF poses"


@pytest.mark.parametrize(
    "target",
    ([np.nan, 0.0, 0.3], [True, 0.0, 0.3], [1e308, 1e308, 1e308]),
)
def test_is_reachable_rejects_invalid_target_with_stable_schema(target) -> None:
    result, _ = reach_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"pos": target},
    )

    assert result["ok"] is False
    assert result["reachable"] is False
    assert result["distance_to_base"] is None
    assert "base_pos" in result


def test_is_reachable_accepts_finite_target() -> None:
    result, _ = reach_policy.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"pos": [0.3, 0.0, 0.0]},
    )

    assert result["ok"] is True
    assert result["reachable"] is True


def test_owlv2_queries_only_the_requested_target(monkeypatch) -> None:
    import torch

    class _NoInventoryEnv:
        def raw_obs(self):
            raise AssertionError("camera-only localization must not read raw_obs")

    class _Batch(dict):
        def to(self, device):
            return self

    class _Processor:
        def __init__(self) -> None:
            self.text = None
            self.truncation = None
            self.max_length = None

        def __call__(
            self,
            *,
            text,
            images,
            return_tensors,
            truncation,
            max_length,
        ):
            self.text = text
            self.truncation = truncation
            self.max_length = max_length
            return _Batch()

        def post_process_object_detection(
            self,
            outputs,
            *,
            threshold,
            target_sizes,
        ):
            return [
                {
                    "boxes": torch.tensor([[10.0, 20.0, 30.0, 40.0]]),
                    "scores": torch.tensor([0.9]),
                    "labels": torch.tensor([0]),
                }
            ]

    class _Model:
        def __call__(self, **kwargs):
            return object()

    processor = _Processor()
    monkeypatch.setattr(
        perception,
        "_load_owlv2",
        lambda: {"proc": processor, "mod": _Model(), "dev": "cpu"},
    )

    uv = perception.locate_by_owlv2(
        _NoInventoryEnv(),
        np.zeros((64, 64, 3), dtype=np.uint8),
        "white mug",
        scale=1,
    )

    assert processor.text == [["a photo of white mug"]]
    assert processor.truncation is True
    assert processor.max_length == 16
    assert uv == (20, 30)


def test_detector_route_refines_owlv2_pixel_with_sam(monkeypatch) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        perception,
        "locate_by_owlv2",
        lambda env, rgb, obj: (10, 12),
    )
    monkeypatch.setattr(
        perception,
        "_sam_refine_point",
        lambda state, uv: (11, 13),
    )

    uv = perception.localize_precise(state, "white mug", route="owlv2")

    assert uv == (11, 13)


def test_sam_refine_exception_falls_back_to_original_pixel(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        perception,
        "sam_mask_at_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("tensor shape mismatch")
        ),
    )

    uv = perception._sam_refine_point(state, (10, 12))

    assert uv == (10, 12)


def test_sam_refine_rejects_large_centroid_jump(monkeypatch) -> None:
    state = SimpleNamespace(env=_Env())
    mask = np.zeros((100, 100), dtype=bool)
    mask[70:80, 70:80] = True
    monkeypatch.setattr(
        perception,
        "sam_mask_at_point",
        lambda *args, **kwargs: mask,
    )

    assert perception._sam_refine_point(state, (10, 12)) == (10, 12)


def test_pointing_route_exception_falls_back_to_detector(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("local pointer shape mismatch")
        ),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("remote pointer shape mismatch")
        ),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(perception, "locate_by_sam3", lambda *args: None)
    monkeypatch.setattr(
        perception,
        "locate_by_owlv2",
        lambda *args: (14, 16),
    )

    uv = perception.localize_precise(
        state,
        "akita black bowl",
        route="vlm_sam",
    )

    assert uv == (14, 16)


def test_pointing_transport_error_propagates_when_all_fallbacks_fail(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    api_connection_error = type("APIConnectionError", (RuntimeError,), {})
    transport = api_connection_error("Connection error.")
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(transport),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(transport),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(perception, "locate_by_sam3", lambda *args: None)
    monkeypatch.setattr(perception, "locate_by_owlv2", lambda *args: None)

    with pytest.raises(api_connection_error):
        perception.localize_precise(
            state,
            "akita black bowl",
            route="vlm_sam",
        )


def test_pointing_transport_error_is_not_masked_by_raw_detector_fallback(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    api_connection_error = type("APIConnectionError", (RuntimeError,), {})
    transport = api_connection_error("Connection error.")
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(transport),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (_ for _ in ()).throw(transport),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: [
            {
                "u": 18,
                "v": 20,
                "bbox": [10, 12, 26, 28],
                "source": "detector:bowl",
            }
        ],
    )
    monkeypatch.setattr(perception, "locate_by_sam3", lambda *args: None)
    monkeypatch.setattr(
        perception,
        "locate_by_owlv2",
        lambda *args: (18, 20),
    )

    with pytest.raises(api_connection_error):
        perception.localize_precise(
            state,
            "akita black bowl",
            route="vlm_sam",
        )


def test_pointing_route_uses_ranked_candidate_verifier(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    candidates = [
        {"u": 10, "v": 12, "bbox": [2, 4, 18, 20], "source": "pointer"},
        {"u": 80, "v": 90, "bbox": [70, 80, 90, 100], "source": "detector"},
    ]
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (10, 12),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: candidates,
    )
    monkeypatch.setattr(
        perception,
        "_choose_localization_candidate",
        lambda *args, **kwargs: (80, 90),
    )
    monkeypatch.setattr(
        perception,
        "_sam_refine_point",
        lambda state, uv: uv,
    )

    assert perception.localize_precise(
        state,
        "cream cheese box",
        route="vlm_sam",
    ) == (80, 90)


def test_pointing_route_prefers_agreeing_pointer_consensus(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (10, 12),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (12, 14),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("detector candidates are unnecessary")
        ),
    )
    monkeypatch.setattr(
        perception,
        "_sam_refine_point",
        lambda state, uv: uv,
    )

    assert perception.localize_precise(
        state,
        "alphabet soup can",
        route="vlm_sam",
    ) == (11, 13)


def test_gpt_pointer_authority_rejects_conflicting_auxiliary_candidate(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setenv("ROBORSI_GPT_POINTER_AUTHORITATIVE", "1")
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (20, 109),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (195, 186),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("authoritative GPT point must bypass candidate fusion")
        ),
    )
    monkeypatch.setattr(
        perception,
        "_sam_refine_point",
        lambda state, uv: (uv[0] + 1, uv[1] + 1),
    )

    assert perception.localize_precise(
        state,
        "plate",
        route="vlm_sam",
    ) == (196, 187)


def test_semantic_point_query_disambiguates_box_from_carton() -> None:
    query = perception._semantic_point_query("cream cheese box")

    assert "cream cheese box" in query
    assert "box-shaped package" in query
    assert "not the tall carton" in query
    assert "bottle" in query
    assert "can" in query


def test_semantic_point_query_distinguishes_cookie_box_from_cabinet() -> None:
    query = perception._semantic_point_query(
        "black bowl on top of the cookie box"
    )

    assert "small low rectangular" in query
    assert "food package" in query
    assert "not a tall cabinet" in query
    assert "preserve every spatial relationship" in query


def test_ranked_candidates_expand_fine_grained_shape_query(
    monkeypatch,
) -> None:
    queries = []

    def _detect(image, query, top_k):
        _ = (image, top_k)
        queries.append(query)
        return []

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        _detect,
    )

    perception._ranked_localization_candidates(
        np.zeros((32, 32, 3), dtype=np.uint8),
        "cream cheese box",
        (10, 12),
    )

    assert "cream cheese box" in queries
    assert "box" in queries
    assert "package" in queries


def test_ranked_relation_bowl_candidates_include_generic_dishes(
    monkeypatch,
) -> None:
    queries = []

    def _detect(image, query, top_k):
        _ = (image, top_k)
        queries.append(query)
        return []

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        _detect,
    )

    perception._ranked_localization_candidates(
        np.zeros((32, 32, 3), dtype=np.uint8),
        "black bowl on top of the cookie box",
        (4, 5),
    )

    assert "dish" in queries


def test_ranked_relation_dish_requests_more_candidates(
    monkeypatch,
) -> None:
    requests = []

    def _detect(image, query, top_k):
        _ = image
        requests.append((query, top_k))
        return []

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        _detect,
    )

    perception._ranked_localization_candidates(
        np.zeros((32, 32, 3), dtype=np.uint8),
        "black bowl on top of the cookie box",
        (4, 5),
    )

    assert ("dish", 8) in requests


def test_depth_relation_verifier_precedes_pointer_consensus(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    candidates = [
        {"u": 14, "v": 104, "bbox": [0, 87, 34, 124], "source": "pointer"},
        {"u": 140, "v": 178, "bbox": [118, 160, 162, 196], "source": "detector:dish"},
    ]
    monkeypatch.setattr(
        perception,
        "local_vlm_point",
        lambda *args, **kwargs: (14, 104),
    )
    monkeypatch.setattr(
        perception,
        "vlm_point",
        lambda *args, **kwargs: (14, 104),
    )
    monkeypatch.setattr(
        perception,
        "_ranked_localization_candidates",
        lambda *args, **kwargs: candidates,
    )
    monkeypatch.setattr(
        perception,
        "_choose_depth_relation_candidate",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        perception,
        "_choose_localization_candidate",
        lambda *args, **kwargs: (140, 178),
    )
    monkeypatch.setattr(
        perception,
        "_sam_refine_point",
        lambda state, uv: uv,
    )

    assert perception.localize_precise(
        state,
        "black bowl on top of the cookie box",
        route="vlm_sam",
    ) == (140, 178)


def test_depth_relation_selector_distinguishes_top_from_top_drawer(
    monkeypatch,
) -> None:
    class _RelationEnv(_Env):
        def take_snapshot(self):
            return SimpleNamespace(
                images={
                    "head_camera": np.zeros(
                        (256, 256, 3),
                        dtype=np.uint8,
                    )
                }
            )

        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (v, camera)
            points = {
                20: np.array([0.0, 0.0, 1.14]),
                68: np.array([0.0, 0.0, 1.07]),
                182: np.array([0.0, 0.0, 0.91]),
            }
            return points[int(u)]

    state = SimpleNamespace(env=_RelationEnv())
    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        lambda *args, **kwargs: [
            SimpleNamespace(
                centroid=(60, 125),
                score=0.9,
                bbox=(0, 70, 120, 180),
            )
        ],
    )
    candidates = [
        {"u": 20, "v": 102, "bbox": [8, 90, 32, 114], "source": "pointer"},
        {"u": 68, "v": 141, "bbox": [43, 121, 92, 160], "source": "detector:bowl"},
        {"u": 182, "v": 126, "bbox": [169, 114, 195, 138], "source": "detector:bowl"},
    ]

    assert perception._choose_depth_relation_candidate(
        state,
        "black bowl on top of the wooden cabinet",
        candidates,
    ) == (20, 102)
    assert perception._choose_depth_relation_candidate(
        state,
        "black bowl in the top layer of the wooden cabinet",
        candidates,
    ) == (68, 141)


def test_depth_relation_selector_fails_when_interior_instance_missing(
    monkeypatch,
) -> None:
    class _RelationEnv(_Env):
        def take_snapshot(self):
            return SimpleNamespace(
                images={
                    "head_camera": np.zeros(
                        (256, 256, 3),
                        dtype=np.uint8,
                    )
                }
            )

        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (v, camera)
            return {
                20: np.array([0.0, 0.0, 1.14]),
                182: np.array([0.0, 0.0, 0.91]),
            }[int(u)]

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        lambda *args, **kwargs: [
            SimpleNamespace(
                centroid=(60, 125),
                score=0.9,
                bbox=(0, 70, 120, 180),
            )
        ],
    )
    state = SimpleNamespace(env=_RelationEnv())
    candidates = [
        {"u": 20, "v": 102, "bbox": [8, 90, 32, 114], "source": "pointer"},
        {"u": 182, "v": 126, "bbox": [169, 114, 195, 138], "source": "detector:bowl"},
    ]

    assert perception._choose_depth_relation_candidate(
        state,
        "black bowl in the top layer of the wooden cabinet",
        candidates,
    ) is None


def test_depth_relation_selector_uses_named_cookie_box_support(
    monkeypatch,
) -> None:
    class _RelationEnv(_Env):
        def take_snapshot(self):
            return SimpleNamespace(
                images={
                    "head_camera": np.zeros(
                        (256, 256, 3),
                        dtype=np.uint8,
                    )
                }
            )

        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (v, camera)
            return {
                14: np.array([0.0, 0.0, 1.14]),
                140: np.array([0.0, 0.0, 0.93]),
            }[int(u)]

    queries = []

    def _detect(image, query, top_k):
        _ = (image, top_k)
        queries.append(query)
        if query == "cookie box":
            return [
                SimpleNamespace(
                    centroid=(140, 182),
                    score=0.9,
                    bbox=(100, 158, 178, 210),
                )
            ]
        return [
            SimpleNamespace(
                centroid=(40, 130),
                score=0.9,
                bbox=(0, 70, 82, 220),
            )
        ]

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        _detect,
    )
    state = SimpleNamespace(env=_RelationEnv())
    candidates = [
        {"u": 14, "v": 104, "bbox": [0, 87, 34, 124], "source": "pointer"},
        {"u": 140, "v": 178, "bbox": [118, 160, 162, 196], "source": "detector:bowl"},
    ]

    assert perception._choose_depth_relation_candidate(
        state,
        "black bowl on top of the cookie box",
        candidates,
    ) == (140, 178)
    assert queries == ["plate", "pot", "cookie box"]


def test_cookie_box_relation_rejects_high_decoy_plate_and_pot(
    monkeypatch,
) -> None:
    class _RelationEnv(_Env):
        def take_snapshot(self):
            return SimpleNamespace(
                images={
                    "head_camera": np.zeros(
                        (256, 256, 3),
                        dtype=np.uint8,
                    )
                }
            )

        def pixel_to_world(self, u, v, camera="agentview"):
            _ = (v, camera)
            return {
                14: np.array([0.0, 0.0, 1.14]),
                140: np.array([0.0, 0.0, 0.93]),
                150: np.array([0.0, 0.0, 0.921]),
                187: np.array([0.0, 0.0, 0.92]),
                195: np.array([0.0, 0.0, 0.909]),
            }[int(u)]

    def _detect(image, query, top_k):
        _ = (image, top_k)
        rows = {
            "cookie box": [
                SimpleNamespace(
                    centroid=(37, 148),
                    score=0.9,
                    bbox=(0, 85, 86, 219),
                )
            ],
            "plate": [
                SimpleNamespace(
                    centroid=(140, 178),
                    score=0.95,
                    bbox=(121, 161, 161, 193),
                ),
                SimpleNamespace(
                    centroid=(195, 183),
                    score=0.9,
                    bbox=(172, 165, 219, 202),
                )
            ],
            "pot": [
                SimpleNamespace(
                    centroid=(14, 104),
                    score=0.95,
                    bbox=(0, 87, 34, 124),
                ),
                SimpleNamespace(
                    centroid=(187, 128),
                    score=0.9,
                    bbox=(175, 116, 200, 140),
                )
            ],
        }
        return rows.get(query, [])

    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        _detect,
    )
    state = SimpleNamespace(env=_RelationEnv())
    candidates = [
        {"u": 14, "v": 104, "bbox": [0, 87, 34, 124], "source": "pointer"},
        {"u": 140, "v": 178, "bbox": [121, 161, 161, 193], "source": "detector:dish"},
        {"u": 150, "v": 200, "bbox": [130, 184, 170, 216], "source": "detector:box"},
        {"u": 187, "v": 128, "bbox": [175, 116, 200, 140], "source": "detector:dish"},
        {"u": 195, "v": 183, "bbox": [172, 165, 219, 202], "source": "detector:dish"},
    ]

    assert perception._choose_depth_relation_candidate(
        state,
        "black bowl on top of the cookie box",
        candidates,
    ) == (140, 178)


def test_grasp_candidates_must_stay_near_segmented_cloud() -> None:
    cloud = np.array(
        [
            [0.00, 0.00, 0.90],
            [0.02, 0.00, 0.91],
            [0.00, 0.02, 0.91],
        ],
        dtype=np.float32,
    )
    grasps = [
        {
            "translation_tcp_world": np.array([0.01, 0.01, 0.91]),
        },
        {
            "translation_tcp_world": np.array([0.13, 0.00, 0.91]),
        },
    ]

    filtered = perception.filter_grasps_consistent_with_cloud(
        grasps,
        cloud,
        max_distance=0.06,
    )

    assert filtered == [grasps[0]]


def test_object_cloud_whole_scene_limit_scales_with_image_area(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType

    camera_utils = ModuleType("robosuite.utils.camera_utils")
    camera_utils.get_camera_transform_matrix = lambda *args, **kwargs: np.eye(4)
    camera_utils.transform_from_pixels_to_world = (
        lambda *args, **kwargs: np.array([0.0, 0.0, 1.0])
    )
    robosuite_utils = ModuleType("robosuite.utils")
    robosuite_utils.camera_utils = camera_utils
    robosuite = ModuleType("robosuite")
    robosuite.utils = robosuite_utils
    monkeypatch.setitem(sys.modules, "robosuite", robosuite)
    monkeypatch.setitem(sys.modules, "robosuite.utils", robosuite_utils)
    monkeypatch.setitem(
        sys.modules,
        "robosuite.utils.camera_utils",
        camera_utils,
    )

    class _CloudEnv:
        def __init__(self, size: int) -> None:
            self._camera_hw = (size, size)
            self._env = SimpleNamespace(
                env=SimpleNamespace(sim=object()),
            )
            self._rgb = np.zeros((size, size, 3), dtype=np.uint8)

        def take_snapshot(self):
            return SimpleNamespace(images={"head_camera": self._rgb})

        def depth_map(self, camera):
            _ = camera
            return np.ones(self._camera_hw, dtype=np.float32)

    def mask_for(size: int) -> np.ndarray:
        mask = np.zeros((size, size), dtype=bool)
        mask.reshape(-1)[:4000] = True
        return mask

    monkeypatch.setattr(perception, "_densest_cluster", lambda points: points)

    env_256 = _CloudEnv(256)
    monkeypatch.setattr(
        perception,
        "sam_mask_at_point",
        lambda *args, **kwargs: mask_for(256),
    )
    assert perception.object_cloud(env_256, 10, 10) is None

    env_512 = _CloudEnv(512)
    monkeypatch.setattr(
        perception,
        "sam_mask_at_point",
        lambda *args, **kwargs: mask_for(512),
    )
    cloud = perception.object_cloud(env_512, 10, 10)
    assert cloud is not None
    assert len(cloud) == 4000


def test_candidate_verifier_uses_zero_based_choice(
    monkeypatch,
    tmp_path,
) -> None:
    state = SimpleNamespace(env=_Env(), workdir=tmp_path)
    candidates = [
        {"u": 10, "v": 12, "bbox": [2, 4, 18, 20], "source": "pointer"},
        {"u": 80, "v": 90, "bbox": [70, 80, 90, 99], "source": "detector"},
    ]
    monkeypatch.setenv(
        "ROBORSI_PERCEPTION_MODEL",
        "anthropic/claude-opus-5",
    )
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.vlm_io._call_vlm_image",
        lambda *args, **kwargs: (
            (
                lambda image: (
                    np.testing.assert_array_equal(
                        np.array(image.shape[:2] > np.array([32, 32])),
                        np.array([True, True]),
                    ),
                    '{"index": 1, "confidence": 0.9}',
                )[1]
            )(
                __import__("cv2").imread(str(args[-1]))
            )
        ),
    )

    selected = perception._choose_localization_candidate(
        state,
        "cream cheese box",
        state.env.take_snapshot().images["head_camera"],
        candidates,
    )

    assert selected == (80, 90)
    assert (tmp_path / "localization_candidates.png").is_file()


def test_candidate_verifier_rejects_low_confidence_choice(
    monkeypatch,
    tmp_path,
) -> None:
    state = SimpleNamespace(env=_Env(), workdir=tmp_path)
    candidates = [
        {"u": 10, "v": 12, "bbox": [2, 4, 18, 20], "source": "pointer"},
        {"u": 20, "v": 22, "bbox": [12, 14, 28, 30], "source": "detector"},
    ]
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.vlm_io._call_vlm_image",
        lambda *args, **kwargs: '{"index": 1, "confidence": 0.3}',
    )

    assert perception._choose_localization_candidate(
        state,
        "cream cheese box",
        state.env.take_snapshot().images["head_camera"],
        candidates,
    ) is None


def test_candidate_verifier_does_not_auto_accept_single_detector(
    tmp_path,
) -> None:
    state = SimpleNamespace(env=_Env(), workdir=tmp_path)
    candidates = [
        {
            "u": 20,
            "v": 22,
            "bbox": [12, 14, 28, 30],
            "source": "detector:box",
        }
    ]

    assert perception._choose_localization_candidate(
        state,
        "cream cheese box",
        state.env.take_snapshot().images["head_camera"],
        candidates,
    ) is None


def test_find_pixel_returns_ranked_candidate_centers(monkeypatch) -> None:
    state = SimpleNamespace(env=_Env())
    detections = [
        SimpleNamespace(
            centroid=(40, 50),
            score=0.8,
            bbox=(30, 40, 50, 60),
        ),
        SimpleNamespace(
            centroid=(80, 90),
            score=0.6,
            bbox=(70, 80, 90, 100),
        ),
    ]
    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        lambda *args, **kwargs: detections,
    )

    result, _ = find_pixel_policy.dispatch_runtime(
        state,
        {"object": "target object"},
    )

    assert result["u"] == 40
    assert result["v"] == 50
    assert result["alternatives"] == [
        {
            "u": 80,
            "v": 90,
            "confidence": 0.6,
            "bbox": [70, 80, 90, 100],
        }
    ]


def test_find_pixel_fine_grained_query_requires_visual_candidate_choice(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env(), workdir=Path("/tmp/find-pixel-test"))
    detections = [
        SimpleNamespace(
            centroid=(40, 50),
            score=0.8,
            bbox=(30, 40, 50, 60),
        ),
        SimpleNamespace(
            centroid=(80, 90),
            score=0.6,
            bbox=(70, 80, 90, 100),
        ),
    ]
    monkeypatch.setattr(
        "roborsi.embodied.skills.base.detect_object.robotwin.policy.detect",
        lambda *args, **kwargs: detections,
    )
    monkeypatch.setattr(
        perception,
        "_choose_localization_candidate",
        lambda *args, **kwargs: None,
    )

    result, _ = find_pixel_policy.dispatch_runtime(
        state,
        {"object": "cream cheese box"},
    )

    assert result["ok"] is False
    assert "identity" in result["reason"]
    assert len(result["alternatives"]) == 2


def test_find_by_detector_rejects_fine_grained_identity(
    monkeypatch,
) -> None:
    state = SimpleNamespace(env=_Env())
    monkeypatch.setattr(
        perception,
        "localize_precise",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fine-grained detector route must not run")
        ),
    )

    result, _ = detector_policy.dispatch_runtime(
        state,
        {"object": "alphabet soup can"},
    )

    assert result["ok"] is False
    assert "find_by_pointing" in result["reason"]


@pytest.mark.parametrize(
    "query",
    (
        "ketchup bottle",
        "milk carton",
        "cream cheese box",
        "cream cheese",
        "alphabet soup can",
        "alphabet soup",
        "tomato sauce",
        "milk",
        "ketchup",
        "butter",
    ),
)
def test_semantic_pointing_required_for_product_identity(query) -> None:
    assert perception._requires_semantic_pointing(query) is True


@pytest.mark.parametrize(
    "query",
    (
        "black bowl",
        "flat stove",
        "wine rack",
        "wooden table",
    ),
)
def test_plain_coarse_object_can_use_detector(query) -> None:
    assert perception._requires_semantic_pointing(query) is False


@pytest.mark.parametrize("module", (detector_policy, pointing_policy))
def test_visual_localizer_rejects_out_of_frame_result(
    monkeypatch,
    module,
) -> None:
    monkeypatch.setattr(
        perception,
        "localize_precise",
        lambda state, obj, route: (99, -4),
    )

    result, _ = module.dispatch_runtime(
        SimpleNamespace(env=_Env()),
        {"object": "white mug"},
    )

    assert result["ok"] is False
    assert result["reason"] == "localizer returned an invalid pixel"
