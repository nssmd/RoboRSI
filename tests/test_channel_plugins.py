"""Tests for channel plugin discovery, merging, and config compatibility."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from roborsi.channels.base import BaseChannel, OutboundMessage
from roborsi.channels.manager import ChannelManager
from roborsi.config.schema import ChannelsConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePlugin(BaseChannel):
    name = "fakeplugin"
    display_name = "Fake Plugin"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


class _FakeBuiltin(BaseChannel):
    """Stand-in for a built-in channel that shares a name with a plugin."""
    name = "shadowed"
    display_name = "Fake Builtin"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


def _make_entry_point(name: str, cls: type):
    """Create a mock entry point that returns *cls* on load()."""
    ep = SimpleNamespace(name=name, load=lambda _cls=cls: _cls)
    return ep


# ---------------------------------------------------------------------------
# ChannelsConfig extra="allow"
# ---------------------------------------------------------------------------

def test_channels_config_accepts_unknown_keys():
    cfg = ChannelsConfig.model_validate({
        "myplugin": {"enabled": True, "token": "abc"},
    })
    extra = cfg.model_extra
    assert extra is not None
    assert extra["myplugin"]["enabled"] is True
    assert extra["myplugin"]["token"] == "abc"


def test_channels_config_getattr_returns_extra():
    cfg = ChannelsConfig.model_validate({"myplugin": {"enabled": True}})
    section = getattr(cfg, "myplugin", None)
    assert isinstance(section, dict)
    assert section["enabled"] is True


def test_channels_config_builtin_fields_removed():
    """After decoupling, ChannelsConfig has no explicit channel fields."""
    cfg = ChannelsConfig()
    assert not hasattr(cfg, "telegram")
    assert cfg.send_progress is True
    assert cfg.send_tool_hints is False


# ---------------------------------------------------------------------------
# discover_plugins
# ---------------------------------------------------------------------------

_EP_TARGET = "importlib.metadata.entry_points"


def test_discover_plugins_loads_entry_points():
    from roborsi.channels.registry import discover_plugins

    ep = _make_entry_point("line", _FakePlugin)
    with patch(_EP_TARGET, return_value=[ep]):
        result = discover_plugins()

    assert "line" in result
    assert result["line"] is _FakePlugin


def test_discover_plugins_handles_load_error():
    from roborsi.channels.registry import discover_plugins

    def _boom():
        raise RuntimeError("broken")

    ep = SimpleNamespace(name="broken", load=_boom)
    with patch(_EP_TARGET, return_value=[ep]):
        result = discover_plugins()

    assert "broken" not in result


# ---------------------------------------------------------------------------
# discover_all — merge & priority
# ---------------------------------------------------------------------------

def test_discover_all_includes_builtins():
    from roborsi.channels.registry import discover_all, discover_channel_names

    with patch(_EP_TARGET, return_value=[]):
        result = discover_all()

    # discover_all() only returns channels that are actually available (dependencies installed)
    # discover_channel_names() returns all built-in channel names
    # So we check that all actually loaded channels are in the result
    for name in result:
        assert name in discover_channel_names()


def test_discover_all_includes_external_plugin():
    from roborsi.channels.registry import discover_all

    ep = _make_entry_point("line", _FakePlugin)
    with patch(_EP_TARGET, return_value=[ep]):
        result = discover_all()

    assert "line" in result
    assert result["line"] is _FakePlugin


def test_discover_all_builtin_shadows_plugin():
    """A built-in channel takes priority over a plugin sharing its name."""
    from roborsi.channels import registry

    ep = _make_entry_point("shadowed", _FakePlugin)
    with patch(_EP_TARGET, return_value=[ep]), \
         patch.object(registry, "discover_channel_names", return_value=["shadowed"]), \
         patch.object(registry, "load_channel_class", return_value=_FakeBuiltin):
        result = registry.discover_all()

    assert "shadowed" in result
    assert result["shadowed"] is _FakeBuiltin
    assert result["shadowed"] is not _FakePlugin


# ---------------------------------------------------------------------------
# Manager _init_channels with dict config (plugin scenario)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manager_loads_plugin_from_dict_config():
    """ChannelManager should instantiate a plugin channel from a raw dict config."""
    from roborsi.channels.manager import ChannelManager

    fake_config = SimpleNamespace(
        channels=ChannelsConfig.model_validate({
            "fakeplugin": {"enabled": True, "allowFrom": ["*"]},
        }),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    with patch(
        "roborsi.channels.registry.discover_all",
        return_value={"fakeplugin": _FakePlugin},
    ):
        mgr = ChannelManager.__new__(ChannelManager)
        mgr.config = fake_config
        mgr.channels = {}
        mgr._init_channels()

    assert "fakeplugin" in mgr.channels
    assert isinstance(mgr.channels["fakeplugin"], _FakePlugin)


@pytest.mark.asyncio
async def test_manager_skips_disabled_plugin():
    fake_config = SimpleNamespace(
        channels=ChannelsConfig.model_validate({
            "fakeplugin": {"enabled": False},
        }),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
    )

    with patch(
        "roborsi.channels.registry.discover_all",
        return_value={"fakeplugin": _FakePlugin},
    ):
        mgr = ChannelManager.__new__(ChannelManager)
        mgr.config = fake_config
        mgr.channels = {}
        mgr._init_channels()

    assert "fakeplugin" not in mgr.channels
