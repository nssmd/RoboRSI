"""Embodiment catalog — aggregates all registries into a unified lookup."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EmbodimentCategory(str, Enum):
    ARM = "arm"
    HAND = "hand"
    HUMANOID = "humanoid"
    MOBILE = "mobile"


@dataclass(frozen=True)
class DeviceInfo:
    """Lightweight device info for catalog API."""
    name: str
    roles: tuple[str, ...] = ()

    def spec_name_for(self, role: str) -> str:
        """Return spec name for a given role, e.g. 'so101_follower'."""
        return f"{self.name}_{role}" if role else self.name


def models_for(category: EmbodimentCategory) -> list[Any]:
    """Return all registered specs/infos for a category."""
    if category == EmbodimentCategory.ARM:
        from roborsi.embodied.embodiment.arm.registry import _PROBES
        return [DeviceInfo(name=name, roles=("follower", "leader")) for name in _PROBES]
    # HAND, HUMANOID, MOBILE — not yet supported in the setup wizard
    return []


def get_spec(name: str) -> Any:
    """Look up any embodiment spec by name, across all registries."""
    from roborsi.embodied.embodiment.arm.registry import get_probe_config
    from roborsi.embodied.embodiment.hand.registry import get_hand_spec

    name = name.lower()
    try:
        cfg = get_probe_config(name)
        return DeviceInfo(name=name, roles=("follower", "leader"))
    except ValueError:
        pass
    try:
        return get_hand_spec(name)
    except ValueError:
        raise ValueError(f"Unknown embodiment model: '{name}'")


def is_supported(category: EmbodimentCategory) -> bool:
    """Return True if the category has registered models."""
    return len(models_for(category)) > 0
