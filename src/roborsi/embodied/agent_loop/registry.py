"""Lazy registry for the single public simulator backend."""

from __future__ import annotations

from collections.abc import Callable

from roborsi.embodied.agent_loop.env import Backend, BackendUnavailable


_REGISTRY: dict[str, Callable[[], Backend]] = {}


def register(name: str, loader: Callable[[], Backend]) -> None:
    _REGISTRY[name] = loader


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str) -> Backend:
    if name not in _REGISTRY:
        raise BackendUnavailable(f"unknown backend {name!r}; known: {list_backends()}")
    backend = _REGISTRY[name]()
    backend.name = name
    return backend


def _load_libero() -> Backend:
    from roborsi.embodied.sim.libero.adapter import LiberoProBackend

    backend = LiberoProBackend(
        suites=("libero_spatial", "libero_object", "libero_goal", "libero_90")
    )
    backend.name = "libero"
    return backend


register("libero", _load_libero)

__all__ = ["get_backend", "list_backends", "register"]
