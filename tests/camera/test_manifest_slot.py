"""CameraRegistry round-trip + validation tests."""

from __future__ import annotations

import pytest

from roborsi.embodied.embodiment.camera import CameraBinding, CameraRegistry


def test_add_list_remove_roundtrip(tmp_path):
    reg = CameraRegistry()
    binding = reg.add(alias="wrist", backend="iphone", udid="UDID-X", notes="left arm")
    assert binding == CameraBinding(alias="wrist", backend="iphone", udid="UDID-X", notes="left arm")

    # Persisted: a fresh registry instance loads the same binding.
    reg2 = CameraRegistry()
    assert [b.alias for b in reg2.list()] == ["wrist"]
    assert reg2.get("wrist") == binding

    reg2.remove("wrist")
    assert CameraRegistry().list() == []


def test_add_rejects_duplicate_alias():
    reg = CameraRegistry()
    reg.add(alias="a", backend="iphone")
    with pytest.raises(ValueError, match="already registered"):
        reg.add(alias="a", backend="iphone")


def test_add_rejects_unknown_backend():
    reg = CameraRegistry()
    with pytest.raises(ValueError, match="Unknown camera backend"):
        reg.add(alias="x", backend="zed2")


def test_remove_unknown_alias():
    reg = CameraRegistry()
    with pytest.raises(ValueError, match="not found"):
        reg.remove("nope")
