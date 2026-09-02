"""Lightweight path resolver — Python 3.10-safe.

Used by sim/data/skill code that runs inside the RoboTwin conda env. The
canonical helpers under ``roborsi.embodied.embodiment.manifest.helpers``
import the full board / channel stack which uses ``StrEnum`` (Python
3.11+) and breaks on 3.10. This module duplicates only what we need:
the home directory and a few derived roots.

Resolution order for HOME:
1. ``ROBORSI_HOME`` env var
2. ``$HOME/.roborsi``
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("ROBORSI_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".roborsi"


def data_root() -> Path:
    env = os.environ.get("ROBORSI_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    return home() / "data"


def datasets_root() -> Path:
    env = os.environ.get("ROBORSI_DATASETS_ROOT")
    if env:
        return Path(env).expanduser()
    return home() / "datasets"


def checkpoints_root() -> Path:
    env = os.environ.get("ROBORSI_CHECKPOINTS_ROOT")
    if env:
        return Path(env).expanduser()
    return home() / "checkpoints"


def evals_root() -> Path:
    env = os.environ.get("ROBORSI_EVALS_ROOT")
    if env:
        return Path(env).expanduser()
    return home() / "evals"


def workspace_skills_root() -> Path:
    env = os.environ.get("ROBORSI_WORKSPACE")
    base = Path(env).expanduser() if env else home() / "workspace"
    return base / "embodied" / "skills"
