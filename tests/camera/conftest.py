"""Shared camera test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _camera_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ROBORSI_HOME so the camera registry lives in tmp."""
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _fake_record3d(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make IPhoneSession load FakeRecord3DStream — no iPhone required."""
    monkeypatch.setenv("ROBORSI_CAMERA_FAKE", "1")
