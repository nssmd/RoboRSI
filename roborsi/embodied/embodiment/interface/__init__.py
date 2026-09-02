from __future__ import annotations

from roborsi.embodied.embodiment.interface.base import Interface
from roborsi.embodied.embodiment.interface.can import CANInterface
from roborsi.embodied.embodiment.interface.serial import SerialInterface
from roborsi.embodied.embodiment.interface.video import VideoInterface

__all__ = ["Interface", "SerialInterface", "VideoInterface", "CANInterface"]
