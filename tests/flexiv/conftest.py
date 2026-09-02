"""Shared Flexiv test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _flexiv_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ROBORSI_HOME so registry/runtime paths live in tmp."""
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _fake_rdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the RdkAdapter to load the fake module (no flexivrdk needed)."""
    monkeypatch.setenv("ROBORSI_FLEXIV_FAKE", "1")
