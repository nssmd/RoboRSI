"""Tests for roborsi.embodied.sim.robotwin.dual_arm_collision.

Tests the pure-Python parts (yaml parsing, sphere math) without
requiring a full SAPIEN sim. The real FK/qpos paths need integration
tests with a live env, which would be done in tests/sim/test_*_e2e.py.
"""
from __future__ import annotations

import numpy as np
import pytest

from roborsi.embodied.sim.robotwin import dual_arm_collision as dac


@pytest.mark.skipif(
    not dac._COLLISION_YML.exists(),
    reason="set ROBORSI_BICOORD_ROOT to run collision-sphere fixture tests",
)
def test_load_sphere_data_parses_both_arms():
    data = dac._sphere_data()
    # Both arms' link spheres present in the shared YAML.
    fl_keys = [k for k in data if k.startswith("fl_")]
    fr_keys = [k for k in data if k.startswith("fr_")]
    assert len(fl_keys) >= 8, f"expected ≥8 fl_ links, got {fl_keys}"
    assert len(fr_keys) >= 8, f"expected ≥8 fr_ links, got {fr_keys}"


@pytest.mark.skipif(
    not dac._COLLISION_YML.exists(),
    reason="set ROBORSI_BICOORD_ROOT to run collision-sphere fixture tests",
)
def test_each_sphere_entry_has_center_and_radius():
    data = dac._sphere_data()
    for link_name, sphs in data.items():
        for s in sphs:
            assert "center" in s, f"{link_name} sphere missing center"
            assert "radius" in s, f"{link_name} sphere missing radius"
            assert len(s["center"]) == 3
            assert isinstance(s["radius"], (int, float))
            assert s["radius"] > 0


def test_held_object_spheres_returns_expected_shapes():
    bowl = dac.held_object_spheres("bowl")
    assert len(bowl) >= 1
    assert all("center" in s and "radius" in s for s in bowl)
    block = dac.held_object_spheres("block")
    assert len(block) >= 1
    none = dac.held_object_spheres("none")
    assert none == []
    unknown = dac.held_object_spheres("car")
    assert unknown == []


def test_arm_link_prefix_mapping():
    assert dac._arm_link_prefix("left") == "fl_"
    assert dac._arm_link_prefix("right") == "fr_"


def test_check_pair_collision_rejects_invalid_arms():
    # No real impl needed — should fail-fast before sim access.
    class _Robot: pass
    class _Impl:
        robot = _Robot()
    result = dac.check_pair_collision(
        impl=_Impl(), holding_arm="left", container_arm="left",
        candidate_qpos=[0]*8,
    )
    assert result["ok"] is False
    assert "must differ" in result["reason"]


def test_check_pair_collision_rejects_bad_arm_name():
    class _Robot: pass
    class _Impl:
        robot = _Robot()
    result = dac.check_pair_collision(
        impl=_Impl(), holding_arm="middle", container_arm="right",
        candidate_qpos=[0]*8,
    )
    assert result["ok"] is False


def test_check_trajectory_collision_rejects_empty_and_wrong_shape():
    class _Robot: pass
    class _Impl:
        robot = _Robot()
    impl = _Impl()
    # 1D shape
    r = dac.check_trajectory_collision(
        impl=impl, holding_arm="left", container_arm="right",
        qpos_trajectory=[1.0, 2.0, 3.0])
    assert r["ok"] is False
    # Empty
    r = dac.check_trajectory_collision(
        impl=impl, holding_arm="left", container_arm="right",
        qpos_trajectory=np.zeros((0, 8)))
    assert r["ok"] is False
