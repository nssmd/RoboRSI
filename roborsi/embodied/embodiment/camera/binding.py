"""CameraBinding — a registered camera entry (alias + backend + udid)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CameraBinding:
    """Persistent record of a registered camera.

    A camera is identified by a stable address that depends on the backend:
      - iphone: Record3D ``udid`` (empty string means "first available")
      - v4l2 (future): /dev/v4l/by-id symlink path
      - hik (future): MV camera serial number
    """

    alias: str
    backend: str            # "iphone" — extend with "v4l2" / "hik" later
    udid: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CameraBinding":
        return cls(
            alias=data["alias"],
            backend=data["backend"],
            udid=data.get("udid", ""),
            notes=data.get("notes", ""),
        )
