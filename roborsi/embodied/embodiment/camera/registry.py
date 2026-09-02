"""Camera backend registry — list of supported backends.

The registry is intentionally tiny right now: only ``iphone`` (Record3D) is
implemented. Adding ``v4l2`` or ``hik`` later means appending here, no
schema migration needed.
"""

from __future__ import annotations


_BACKENDS: tuple[str, ...] = ("iphone",)


def all_backends() -> tuple[str, ...]:
    return _BACKENDS


def assert_backend(name: str) -> None:
    if name not in _BACKENDS:
        raise ValueError(f"Unknown camera backend '{name}'. Valid: {list(_BACKENDS)}")
