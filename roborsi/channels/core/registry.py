"""Platform registry: adapters self-register instead of living in an if/elif.

Taken from the hermes gateway, which carries 21 platforms this way. The point
is not the indirection — it is that adding Telegram must not require editing a
dispatch chain in the core, because that chain is exactly where per-platform
assumptions accumulate until the "generic" agent has thirty branches in it.

An entry declares three things separately, and they fail at different times:

  available()  are the deps installed?      -> hide from `list`, tell the user
  configured() are the tokens present?      -> show as "needs setup"
  build()      construct it                 -> may still fail on connect

Keeping them apart is what lets `roborsi channels` print an honest table
rather than crashing on the first platform whose SDK is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .ports import InboundPort


@dataclass
class PlatformEntry:
    name: str
    label: str
    build: Callable[[dict[str, Any]], InboundPort]
    available: Callable[[], bool] = lambda: True
    configured: Callable[[], bool] = lambda: True
    required_env: list[str] = field(default_factory=list)
    install_hint: str = ""
    supports_cards: bool = False
    supports_files: bool = False


class PlatformRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, PlatformEntry] = {}

    def register(self, entry: PlatformEntry) -> None:
        if entry.name in self._entries:
            raise ValueError(f"platform {entry.name!r} already registered")
        self._entries[entry.name] = entry

    def get(self, name: str) -> PlatformEntry | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def create(self, name: str, config: dict[str, Any] | None = None) -> InboundPort:
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"unknown platform {name!r}; have {self.names()}")
        if not entry.available():
            hint = f"  ({entry.install_hint})" if entry.install_hint else ""
            raise RuntimeError(f"platform {name!r} dependencies missing{hint}")
        if not entry.configured():
            need = ", ".join(entry.required_env) or "credentials"
            raise RuntimeError(f"platform {name!r} not configured; needs {need}")
        return entry.build(config or {})

    def table(self) -> list[dict[str, Any]]:
        """One row per platform for `roborsi channels`."""
        rows = []
        for name in self.names():
            e = self._entries[name]
            ok = e.available()
            rows.append({
                "name": name, "label": e.label,
                "status": "ready" if ok and e.configured()
                          else "needs setup" if ok else "not installed",
                "cards": e.supports_cards, "files": e.supports_files,
                "env": e.required_env,
            })
        return rows


registry = PlatformRegistry()


def load_builtin_platforms() -> None:
    """Import the bundled adapters so their module-level register() runs.

    Import errors are swallowed on purpose: a missing telegram SDK should make
    that one platform unavailable, not stop the CLI from starting.
    """
    from importlib import import_module

    for mod in ("cli", "feishu", "telegram", "web"):
        try:
            import_module(f"roborsi.channels.platforms.{mod}")
        except ImportError:
            continue
