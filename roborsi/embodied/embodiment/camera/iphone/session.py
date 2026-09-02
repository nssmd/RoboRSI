"""IPhoneSession — open Record3D stream, capture one RGB frame, close.

Designed for one-shot snapshots. Streaming/multi-frame capture should
build a separate component on top of this — keeping snapshot dead simple
makes it cheap to call from the CLI and from the AI tool.

Lifecycle (mirrors SuperInference/devices/iphone.py):
    enumerate    Record3DStream.get_connected_devices()
    connect      session.connect(dev) + register on_new_frame callback
    snapshot     wait for first frame event → session.get_rgb_frame()
    teardown     session.stop_stream()

If ``ROBORSI_CAMERA_FAKE=1`` is set, a deterministic in-memory stream is
substituted. Use that for tests; the rest of the API is identical.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np


def _load_record3d():
    if os.environ.get("ROBORSI_CAMERA_FAKE") == "1":
        from roborsi.embodied.embodiment.camera.iphone.fake import FakeRecord3DStream
        return FakeRecord3DStream
    from record3d import Record3DStream
    return Record3DStream


@dataclass(frozen=True)
class IPhoneDeviceInfo:
    udid: str
    product_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"udid": self.udid, "product_id": self.product_id}


def list_devices() -> list[IPhoneDeviceInfo]:
    """Return all currently-connected Record3D devices.

    The Record3D iOS app does *not* need to be in streaming mode for the
    device to enumerate, but it does need to be installed and the phone
    has to trust this Mac/PC.
    """
    stream_cls = _load_record3d()
    out: list[IPhoneDeviceInfo] = []
    for d in stream_cls.get_connected_devices():
        udid_raw = getattr(d, "udid", "") or ""
        pid_raw = getattr(d, "product_id", "") or ""
        out.append(IPhoneDeviceInfo(udid=str(udid_raw), product_id=str(pid_raw)))
    return out


class IPhoneSnapshotError(RuntimeError):
    """Raised when iPhone capture fails (no device, no frame, etc.)."""


class IPhoneSession:
    """Single-snapshot iPhone camera session. Use as a context manager."""

    def __init__(self, udid: str = "", rgb_timeout: float = 10.0, flip_truedepth: bool = True) -> None:
        self._stream_cls = _load_record3d()
        self._requested_udid = udid
        self._rgb_timeout = rgb_timeout
        self._flip_truedepth = flip_truedepth

        self._session: Any | None = None
        self._frame_event = threading.Event()
        self._connected_udid: str = ""
        self._connected_product_id: str = ""

    def __enter__(self) -> "IPhoneSession":
        dev = self._select_device()
        self._connected_udid = getattr(dev, "udid", "") or ""
        self._connected_product_id = getattr(dev, "product_id", "") or ""
        self._session = self._stream_cls()
        self._session.on_new_frame = self._on_new_frame
        self._session.on_stream_stopped = self._on_stream_stopped
        self._session.connect(dev)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._session is not None:
            _safe_disconnect(self._session)
            self._session = None

    @property
    def connected_udid(self) -> str:
        return self._connected_udid

    @property
    def connected_product_id(self) -> str:
        return self._connected_product_id

    def snapshot(self, drop_first: int = 8) -> np.ndarray:
        """Block until a fresh frame arrives (or ``rgb_timeout`` elapses).

        The Record3D SDK delivers a cached "old" frame right after ``connect``
        — any scene change that happened during our reconnect isn't reflected
        until a few live frames flow through. We drop the first ``drop_first``
        frames to guarantee the returned frame is from the current pose.
        """
        if self._session is None:
            raise IPhoneSnapshotError("snapshot() called outside an active session")
        for _ in range(drop_first):
            self._frame_event.clear()
            if not self._frame_event.wait(timeout=self._rgb_timeout):
                raise IPhoneSnapshotError(
                    f"No frame from iPhone within {self._rgb_timeout}s. "
                    "Is the Record3D app open and streaming on the phone?"
                )
        # One more wait so we grab the freshest frame after the drop.
        self._frame_event.clear()
        if not self._frame_event.wait(timeout=self._rgb_timeout):
            raise IPhoneSnapshotError(
                f"No frame from iPhone within {self._rgb_timeout}s. "
                "Is the Record3D app open and streaming on the phone?"
            )
        frame = self._session.get_rgb_frame()
        if frame is None:
            raise IPhoneSnapshotError("Record3D returned an empty frame")
        if (
            self._flip_truedepth
            and hasattr(self._session, "get_device_type")
            and self._session.get_device_type() == 0  # TrueDepth
        ):
            import cv2
            frame = cv2.flip(frame, 1)
        return frame

    def _select_device(self) -> Any:
        devices = self._stream_cls.get_connected_devices()
        if not devices:
            raise IPhoneSnapshotError(
                "No Record3D device detected. Plug in the iPhone, open the "
                "Record3D app, and trust this computer."
            )
        if self._requested_udid:
            for dev in devices:
                if getattr(dev, "udid", None) == self._requested_udid:
                    return dev
            raise IPhoneSnapshotError(
                f"Record3D device with udid '{self._requested_udid}' not found. "
                f"Available: {[getattr(d, 'udid', '') for d in devices]}"
            )
        return devices[0]

    def _on_new_frame(self) -> None:
        self._frame_event.set()

    def _on_stream_stopped(self) -> None:
        self._frame_event.set()


def _safe_disconnect(session: Any) -> None:
    """Teardown an open Record3DStream. Real SDK uses ``disconnect()``, our fake uses ``stop_stream()``."""
    for method in ("disconnect", "stop_stream"):
        fn = getattr(session, method, None)
        if callable(fn):
            fn()
            return
