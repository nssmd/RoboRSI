"""Feishu/Lark channel — synchronous, wraps the existing lark_oapi
WebSocket bot. Drop-in for `roborsi.channels.agent.Channel`."""

from roborsi.channels.agent.feishu.bot_ws import serve as run_feishu_bot

__all__ = ["run_feishu_bot"]
