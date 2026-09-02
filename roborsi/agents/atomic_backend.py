"""Atomic-task → (backend, sim-task) resolution for the 3-role path.

The 3-role atomic pipeline (``_run_atomic_3role``) historically assumed the
RoboTwin backend: the atomic NAME doubled as the sim env name and the Engineer
opened ``get_backend("robotwin")``. Non-RoboTwin atomics (LIBERO-PRO) break both
assumptions — their sim task is a ``<suite>/<id>`` string (``libero_object/0``)
that differs from the skill name, and they must run on the ``libero-pro`` /
``libero`` backend.

An atomic declares this in its own ``SKILL.md`` frontmatter under ``metadata``:

    metadata:
      backends: [libero, libero-pro]   # first entry = default backend
      libero_task: libero_object/0     # the sim task passed to make_env

This module is the single place that reads that declaration. RoboTwin atomics
carry no such metadata → they resolve to ``("robotwin", <atomic>)``, keeping the
existing path byte-for-byte identical.
"""
from __future__ import annotations

from dataclasses import dataclass


# Backends whose sim task is NOT the atomic name — the atomic maps to a
# ``<suite>/<id>`` LIBERO task via the SKILL.md ``libero_task`` field.
_LIBERO_BACKENDS = {"libero", "libero-pro"}


@dataclass(frozen=True)
class AtomicBackend:
    """How to run one atomic: which sim backend, and which sim task string
    to hand ``backend.make_env`` (may differ from the atomic/skill name)."""

    backend_name: str
    sim_task: str

    @property
    def is_robotwin(self) -> bool:
        return self.backend_name in ("robotwin", "bicoord")

    @property
    def needs_robotwin_env(self) -> bool:
        """RoboTwin/BiCoord atomics need an ``envs/<task>.py`` sim env authored
        (the env-synthesis preflight). LIBERO atomics do not — their env is the
        LIBERO benchmark itself, addressed by ``sim_task``."""
        return self.is_robotwin


def resolve(atomic: str) -> AtomicBackend:
    """Read the atomic's SKILL.md frontmatter and decide (backend, sim_task).

    Falls back to ``("robotwin", atomic)`` when the atomic declares nothing —
    so every RoboTwin/BiCoord atomic keeps the legacy behaviour unchanged.

    For a LIBERO atomic, two env vars let an eval/campaign harness point the
    same generic pick-place skill at any of the 200 benchmark tasks without
    editing SKILL.md:

      * ``ROBORSI_LIBERO_BACKEND`` — ``libero`` (vanilla) or ``libero-pro``
        (perturbed). Defaults to the SKILL.md's first declared backend.
      * ``ROBORSI_LIBERO_TASK`` — a ``<suite>/<id>`` task string, e.g.
        ``libero_object_object/0``. Defaults to SKILL.md ``libero_task``.
    """
    import os
    from roborsi.embodied.skills import get as get_skill

    meta = _skill_metadata(atomic, get_skill)
    backends = meta.get("backends")
    backend = backends[0] if isinstance(backends, list) and backends else "robotwin"

    if backend in _LIBERO_BACKENDS:
        backend = os.environ.get("ROBORSI_LIBERO_BACKEND", backend)
        sim_task = os.environ.get(
            "ROBORSI_LIBERO_TASK", str(meta.get("libero_task") or atomic)
        ).strip()
        return AtomicBackend(backend_name=backend, sim_task=sim_task)

    return AtomicBackend(backend_name="robotwin", sim_task=atomic)


def _skill_metadata(atomic: str, get_skill) -> dict:
    """Frontmatter ``metadata`` dict for ``<atomic>`` or ``<atomic>.zeroshot``.
    Returns {} when neither is found or metadata is malformed."""
    for name in (atomic, f"{atomic}.zeroshot"):
        sk = get_skill(name)
        if sk is None or not sk.frontmatter:
            continue
        meta = sk.frontmatter.get("metadata")
        if isinstance(meta, dict):
            return meta
    return {}
