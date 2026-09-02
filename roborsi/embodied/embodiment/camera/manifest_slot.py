"""Camera registry file — sibling of flexiv.json.

Stored at ``~/.roborsi/workspace/embodied/camera.json``. Same on-disk
layout convention as ``FlexivRegistry`` — keep them parallel so tooling
(backups, dump, validate) treats them uniformly.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from roborsi.embodied.embodiment.camera.binding import CameraBinding
from roborsi.embodied.embodiment.camera.registry import assert_backend
from roborsi.embodied.embodiment.manifest.helpers import get_roborsi_home


def get_camera_registry_path(home: Path | None = None) -> Path:
    return (home or get_roborsi_home()) / "workspace" / "embodied" / "camera.json"


class CameraRegistry:
    """Reads and writes camera.json — list of registered cameras."""

    _VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or get_camera_registry_path()
        self._lock = Lock()
        self._cameras: dict[str, CameraBinding] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for item in data.get("cameras", []):
            binding = CameraBinding.from_dict(item)
            self._cameras[binding.alias] = binding

    def _persist(self) -> None:
        snapshot = {
            "version": self._VERSION,
            "cameras": [b.to_dict() for b in self._cameras.values()],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, str(self._path))
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def list(self) -> list[CameraBinding]:
        with self._lock:
            return list(self._cameras.values())

    def get(self, alias: str) -> CameraBinding | None:
        with self._lock:
            return self._cameras.get(alias)

    def add(self, alias: str, backend: str, udid: str = "", notes: str = "") -> CameraBinding:
        if not alias:
            raise ValueError("alias is required")
        assert_backend(backend)
        with self._lock:
            if alias in self._cameras:
                raise ValueError(f"alias '{alias}' already registered")
            binding = CameraBinding(alias=alias, backend=backend, udid=udid, notes=notes)
            self._cameras[alias] = binding
            self._persist()
        return binding

    def remove(self, alias: str) -> None:
        with self._lock:
            if alias not in self._cameras:
                raise ValueError(f"alias '{alias}' not found")
            del self._cameras[alias]
            self._persist()
