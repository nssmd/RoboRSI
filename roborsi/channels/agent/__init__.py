"""Agent-driven channels.

Distinct from the bus-based ``roborsi.channels.*`` modules: these
channels run the synchronous ``bot_agent.handle_user_message`` loop
directly, with the channel object responsible for IO (read input, send
text / cards / files back).

Channels in this subpackage share a single sync interface so that the
same agent loop can drive Feishu (production), a terminal (demos /
recordings), or a future web chat — with the channel choice picked at
``roborsi start --channel=...`` time."""

from roborsi.channels.agent.base import Channel, ChannelCtx

__all__ = ["Channel", "ChannelCtx"]
