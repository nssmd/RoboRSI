"""Fake Record3DStream — used for tests and dev when no iPhone is plugged in.

Activate with ``ROBORSI_CAMERA_FAKE=1``. Exposes the subset of the
real ``Record3DStream`` API we actually use in :class:`IPhoneSession`,
nothing more.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np


class _FakeDevice:
    def __init__(self, udid: str, product_id: str = "fake-iphone") -> None:
        self.udid = udid
        self.product_id = product_id


class FakeRecord3DStream:
    """Stand-in for ``record3d.Record3DStream`` in tests."""

    DEVICE_TYPE_TRUEDEPTH = 0
    DEVICE_TYPE_LIDAR = 1
    FRAME_INTERVAL_S = 0.01

    _devices: list[_FakeDevice] = [_FakeDevice("FAKE-UDID-0001")]
    _device_type: int = DEVICE_TYPE_LIDAR

    def __init__(self) -> None:
        self.on_new_frame: Any = None
        self.on_stream_stopped: Any = None
        self._connected = False
        self._timer: threading.Timer | None = None

    @classmethod
    def get_connected_devices(cls) -> list[_FakeDevice]:
        return list(cls._devices)

    def connect(self, dev: _FakeDevice) -> None:
        self._connected = True
        self._schedule_frame()

    def _schedule_frame(self) -> None:
        if not self._connected:
            return
        self._timer = threading.Timer(self.FRAME_INTERVAL_S, self._fire_frame)
        self._timer.daemon = True
        self._timer.start()

    def _fire_frame(self) -> None:
        if not self._connected:
            return
        if self.on_new_frame is not None:
            self.on_new_frame()
        self._schedule_frame()

    def get_device_type(self) -> int:
        return self._device_type

    def get_rgb_frame(self) -> np.ndarray:
        # Solid colour with a 4 px coloured border so tests can assert on shape + content.
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:4, :, 0] = 255      # red top
        frame[-4:, :, 1] = 255     # green bottom
        frame[:, :4, 2] = 255      # blue left
        frame[:, -4:, :] = 255     # white right
        return frame

    def stop_stream(self) -> None:
        self._connected = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self.on_stream_stopped is not None:
            self.on_stream_stopped()

    # Mirror the real record3d API — some versions expose disconnect().
    disconnect = stop_stream
