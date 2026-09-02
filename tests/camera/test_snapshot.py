"""End-to-end snapshot test using the fake Record3DStream backend."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from roborsi.embodied.embodiment.camera import CameraRegistry
from roborsi.embodied.embodiment.camera.iphone import (
    IPhoneSession,
    list_devices,
)


def test_list_devices_returns_fake():
    devices = list_devices()
    assert len(devices) >= 1
    assert devices[0].udid == "FAKE-UDID-0001"


def test_iphone_session_snapshot_returns_rgb_frame():
    with IPhoneSession(rgb_timeout=2.0) as session:
        frame = session.snapshot()
    assert frame.dtype == np.uint8
    assert frame.shape == (480, 640, 3)


def test_cli_snapshot_writes_file(tmp_path: Path):
    """Same code path the CLI uses: registry.add → IPhoneSession → cv2.imwrite."""
    reg = CameraRegistry()
    reg.add(alias="wrist", backend="iphone", udid="FAKE-UDID-0001")

    out = tmp_path / "shot.jpg"
    binding = reg.get("wrist")
    assert binding is not None

    with IPhoneSession(udid=binding.udid, rgb_timeout=2.0) as session:
        rgb = session.snapshot()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    assert cv2.imwrite(str(out), bgr)

    assert out.exists() and out.stat().st_size > 0
    loaded = cv2.imread(str(out))
    assert loaded is not None
    assert loaded.shape == (480, 640, 3)
