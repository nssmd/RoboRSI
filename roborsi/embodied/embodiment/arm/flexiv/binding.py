"""FlexivBinding — a registered Flexiv robot entry (alias + SN + model)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FlexivBinding:
    """Persistent record of a registered Flexiv robot.

    Unlike the LeRobot Binding (which points at a USB serial port), Flexiv
    robots are identified by controller serial number over TCP — no local
    device path needed.
    """

    alias: str
    sn: str
    model: str = "Rizon4"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlexivBinding":
        return cls(
            alias=data["alias"],
            sn=data["sn"],
            model=data.get("model", "Rizon4"),
        )
