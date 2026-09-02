"""Chat channels module with plugin architecture."""

from roborsi.channels.base import BaseChannel
from roborsi.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
