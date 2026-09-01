"""Python 3.10-safe paths for the standalone LIBERO runtime.

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


def workspace_skills_root() -> Path:
    env = os.environ.get("ROBORSI_WORKSPACE")
    base = Path(env).expanduser() if env else home() / "workspace"
    return base / "embodied" / "skills"
