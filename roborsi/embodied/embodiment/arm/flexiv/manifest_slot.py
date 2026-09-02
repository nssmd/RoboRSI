"""Flexiv registry file — a sibling of manifest.json for Flexiv robots.

Stored at ``~/.roborsi/workspace/embodied/flexiv.json``. Kept in its own
file so the main manifest validator stays untouched (see
``manifest/helpers._VALID_TOP_KEYS``).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from roborsi.embodied.embodiment.arm.flexiv.binding import FlexivBinding
from roborsi.embodied.embodiment.arm.flexiv.registry import all_models
from roborsi.embodied.embodiment.manifest.helpers import get_roborsi_home


def get_flexiv_registry_path(home: Path | None = None) -> Path:
    return (home or get_roborsi_home()) / "workspace" / "embodied" / "flexiv.json"


def get_flexiv_runtime_dir(home: Path | None = None) -> Path:
    """Runtime dir for session sockets, pid files, and logs."""
    path = (home or get_roborsi_home()) / "flexiv"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FlexivRegistry:
    """Reads and writes flexiv.json — list of registered robots."""

    _VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or get_flexiv_registry_path()
        self._lock = Lock()
        self._robots: dict[str, FlexivBinding] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        for item in data.get("robots", []):
            binding = FlexivBinding.from_dict(item)
            self._robots[binding.alias] = binding

    def _persist(self) -> None:
        snapshot = {
            "version": self._VERSION,
            "robots": [b.to_dict() for b in self._robots.values()],
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

    def list(self) -> list[FlexivBinding]:
        with self._lock:
            return list(self._robots.values())

    def get(self, alias: str) -> FlexivBinding | None:
        with self._lock:
            return self._robots.get(alias)

    def add(self, alias: str, sn: str, model: str = "Rizon4") -> FlexivBinding:
        if not alias:
            raise ValueError("alias is required")
        if not sn:
            raise ValueError("sn is required")
        if model not in all_models():
            raise ValueError(f"Unknown Flexiv model '{model}'. Valid: {list(all_models())}")
        with self._lock:
            if alias in self._robots:
                raise ValueError(f"alias '{alias}' already registered")
            binding = FlexivBinding(alias=alias, sn=sn, model=model)
            self._robots[alias] = binding
            self._persist()
        return binding

    def remove(self, alias: str) -> None:
        with self._lock:
            if alias not in self._robots:
                raise ValueError(f"alias '{alias}' not found")
            del self._robots[alias]
            self._persist()
