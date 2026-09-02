"""Camera subsystem — alias registry + per-backend snapshot sessions.

The CLI (``roborsi camera ...``) and the AI tool (``CameraToolGroup``)
both go through :class:`CameraRegistry` for the alias→binding map and
through backend-specific session classes (e.g. ``IPhoneSession``) for the
actual capture. No sidecar — snapshot is fast enough to run inline.
"""

from roborsi.embodied.embodiment.camera.binding import CameraBinding
from roborsi.embodied.embodiment.camera.manifest_slot import (
    CameraRegistry,
    get_camera_registry_path,
)
from roborsi.embodied.embodiment.camera.registry import all_backends, assert_backend

__all__ = [
    "CameraBinding",
    "CameraRegistry",
    "all_backends",
    "assert_backend",
    "get_camera_registry_path",
]
