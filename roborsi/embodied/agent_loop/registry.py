"""Backend registry — the single place that enumerates every embodiment
(sim + future real). Lives in the neutral ``agent_loop`` layer, NOT under
``sim/``, because a real-robot backend is not a sim yet registers here too.

Backends are discovered lazily so optional heavy deps (SAPIEN, cuRobo, Torch,
robosuite) don't blow up ``import roborsi`` on boxes without them:

    from roborsi.embodied.agent_loop import get_backend
    env = get_backend("robotwin").make_env("beat_block_hammer")
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from roborsi.embodied.agent_loop.env import Backend, BackendUnavailable


_REGISTRY: dict[str, Callable[[], Backend]] = {}


def register(name: str, loader: Callable[[], Backend]) -> None:
    _REGISTRY[name] = loader


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str) -> Backend:
    if name not in _REGISTRY:
        raise BackendUnavailable(
            f"unknown backend '{name}'. known: {list_backends()}"
        )
    backend = _REGISTRY[name]()
    backend.name = name
    return backend


# ── Built-in registrations (loaders stay lazy → no import-time sim dep) ──
def _load_robotwin() -> Backend:
    from roborsi.embodied.sim.robotwin.adapter import RoboTwinBackend
    return RoboTwinBackend()


def _load_robotwin_http() -> Backend:
    from roborsi.embodied.sim.robotwin.client import HttpRobotwinBackend
    return HttpRobotwinBackend()


def _load_bicoord() -> Backend:
    """BiCoord-Bench is a RoboTwin fork; same adapter, different task_root."""
    import os
    from roborsi.embodied.sim.robotwin.adapter import RoboTwinBackend
    root = os.environ.get(
        "ROBORSI_BICOORD_ROOT",
        str(Path.home() / "BiCoord-Bench"),
    )
    be = RoboTwinBackend(task_root=root)
    be.name = "bicoord"
    return be


def _load_robocasa() -> Backend:
    from roborsi.embodied.sim.robocasa.adapter import RoboCasaBackend
    return RoboCasaBackend()


def _load_libero_pro() -> Backend:
    """LIBERO-PRO: perturbed LIBERO suites (task/object/swap/language)."""
    from roborsi.embodied.sim.libero.adapter import LiberoProBackend
    return LiberoProBackend()


def _load_libero() -> Backend:
    """Vanilla LIBERO: the un-perturbed base suites, same adapter."""
    from roborsi.embodied.sim.libero.adapter import LiberoProBackend
    be = LiberoProBackend(
        suites=("libero_goal", "libero_spatial", "libero_object", "libero_10")
    )
    be.name = "libero"
    return be


register("robotwin", _load_robotwin)
register("robotwin-http", _load_robotwin_http)
register("bicoord", _load_bicoord)
register("robocasa", _load_robocasa)
register("libero-pro", _load_libero_pro)
register("libero", _load_libero)


__all__ = ["register", "list_backends", "get_backend"]
