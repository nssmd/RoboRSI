from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    clear_visual_hold,
    get_pending_visual_hold,
    get_visual_hold,
    record_pending_visual_hold,
    record_visual_hold,
    verify_visual_hold,
)


def _frames() -> tuple[np.ndarray, np.ndarray]:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    after = before.copy()
    after[8:24, 8:24] = 200
    return before, after


def test_unchanged_source_does_not_record_visual_hold() -> None:
    env = SimpleNamespace()
    before, _ = _frames()

    evidence = record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=before.copy(),
    )

    assert evidence is None
    assert get_visual_hold(env) is None


def test_changed_source_records_and_verifies_visual_hold() -> None:
    env = SimpleNamespace()
    before, after = _frames()

    evidence = record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )
    verified = verify_visual_hold(env, after.copy())

    assert evidence is not None
    assert evidence.object_name == "white mug"
    assert evidence.source_mad > 3.0
    assert verified.ok is True
    assert verified.current_source_mad > 3.0


def test_visual_hold_records_object_center_offset() -> None:
    env = SimpleNamespace()
    before, after = _frames()

    evidence = record_visual_hold(
        env,
        object_name="akita black bowl",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
        object_offset_local=(0.03, -0.04, 0.0),
        release_clearance_hint=0.04,
    )

    assert evidence is not None
    assert evidence.object_offset_local == (0.03, -0.04, 0.0)
    assert evidence.release_clearance_hint == 0.04


def test_visual_hold_identity_requires_independent_verification() -> None:
    env = SimpleNamespace()
    before, after = _frames()

    unverified = record_visual_hold(
        env,
        object_name="alphabet soup can",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )
    verified = record_visual_hold(
        env,
        object_name="alphabet soup can",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
        identity_verified=True,
    )

    assert unverified is not None
    assert unverified.identity_verified is False
    assert verified is not None
    assert verified.identity_verified is True


def test_source_reoccupied_invalidates_visual_hold() -> None:
    env = SimpleNamespace()
    before, after = _frames()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )

    verified = verify_visual_hold(env, before.copy())

    assert verified.ok is False
    assert verified.reason == "source_patch_reoccupied"


def test_source_changed_but_still_closer_to_occupied_state_is_rejected() -> None:
    env = SimpleNamespace()
    before, after = _frames()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )
    shifted = before.copy()
    shifted[8:24, 8:24] = 10

    verified = verify_visual_hold(env, shifted)

    assert verified.current_source_mad > 3.0
    assert verified.current_to_after_mad > verified.current_source_mad
    assert verified.ok is False
    assert verified.reason == "source_patch_reoccupied"


def test_clear_visual_hold_removes_evidence() -> None:
    env = SimpleNamespace()
    before, after = _frames()
    record_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        after_rgb=after,
    )

    clear_visual_hold(env)

    assert get_visual_hold(env) is None
    verified = verify_visual_hold(env, after)
    assert verified.ok is False
    assert verified.reason == "missing_visual_hold_evidence"


def test_edge_pending_hold_promotes_when_rgb_changes_and_depth_recedes() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    current = before.copy()
    current[4:24, :20] = 180
    before_depth = np.full((32, 32), 0.42, dtype=np.float32)
    current_depth = np.full((32, 32), 0.58, dtype=np.float32)

    class _Env:
        def depth_map(self, camera="agentview"):
            _ = camera
            return current_depth

    env = _Env()
    pending = record_pending_visual_hold(
        env,
        object_name="akita black bowl",
        source_pixel=(9, 16),
        before_rgb=before,
        before_depth=before_depth,
    )
    verified = verify_visual_hold(env, current, holding=True)

    assert pending is not None
    assert verified.ok is True
    assert verified.reason == "pending_visual_hold_promoted"
    assert get_pending_visual_hold(env) is None
    assert get_visual_hold(env) is not None


def test_pending_hold_rejects_rgb_change_when_depth_moves_closer() -> None:
    before = np.zeros((32, 32, 3), dtype=np.uint8)
    current = before.copy()
    current[8:24, 8:24] = 180
    before_depth = np.full((32, 32), 0.42, dtype=np.float32)
    current_depth = np.full((32, 32), 0.30, dtype=np.float32)

    class _Env:
        def depth_map(self, camera="agentview"):
            _ = camera
            return current_depth

    env = _Env()
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        before_depth=before_depth,
    )

    verified = verify_visual_hold(env, current, holding=True)

    assert verified.ok is False
    assert verified.reason == "pending_source_depth_not_cleared"
    assert get_visual_hold(env) is None
    assert get_pending_visual_hold(env) is not None


def test_pending_hold_requires_current_depth() -> None:
    before, after = _frames()

    class _Env:
        def depth_map(self, camera="agentview"):
            _ = camera
            return None

    env = _Env()
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        before_depth=np.full((32, 32), 0.42, dtype=np.float32),
    )

    verified = verify_visual_hold(env, after, holding=True)

    assert verified.ok is False
    assert verified.reason == "pending_source_depth_unavailable"


def test_clear_visual_hold_removes_pending_evidence() -> None:
    env = SimpleNamespace()
    before, _ = _frames()
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        before_depth=np.full((32, 32), 0.42, dtype=np.float32),
    )

    clear_visual_hold(env)

    assert get_pending_visual_hold(env) is None


def test_pending_hold_does_not_promote_without_confirmed_hold() -> None:
    before, after = _frames()
    current_depth = np.full((32, 32), 0.58, dtype=np.float32)

    class _Env:
        def depth_map(self, camera="agentview"):
            _ = camera
            return current_depth

    env = _Env()
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        before_depth=np.full((32, 32), 0.42, dtype=np.float32),
    )

    verified = verify_visual_hold(env, after, holding=False)

    assert verified.ok is False
    assert verified.reason == "pending_hold_not_confirmed"
    assert get_visual_hold(env) is None


def test_pending_hold_requires_center_depth_support_to_recede() -> None:
    before, after = _frames()
    current_depth = np.full((32, 32), 0.58, dtype=np.float32)
    current_depth[12:21, 12:21] = 0.42

    class _Env:
        def depth_map(self, camera="agentview"):
            _ = camera
            return current_depth

    env = _Env()
    record_pending_visual_hold(
        env,
        object_name="white mug",
        source_pixel=(16, 16),
        before_rgb=before,
        before_depth=np.full((32, 32), 0.42, dtype=np.float32),
    )

    verified = verify_visual_hold(env, after, holding=True)

    assert verified.ok is False
    assert verified.reason == "pending_source_depth_not_cleared"
    assert get_visual_hold(env) is None
