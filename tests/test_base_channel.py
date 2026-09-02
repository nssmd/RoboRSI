from types import SimpleNamespace

from roborsi.channels.base import BaseChannel, OutboundMessage


class _DummyChannel(BaseChannel):
    name = "dummy"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        return None


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["allow@email.com"]))

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


def test_is_allowed_supports_dict_config() -> None:
    channel = _DummyChannel({"allow_from": ["*"]})

    assert channel.is_allowed("web:user") is True
