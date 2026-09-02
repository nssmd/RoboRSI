"""Fake flexivrdk implementation for development without hardware.

Activated when ``ROBORSI_FLEXIV_FAKE=1``. Mimics the subset of the real
API used by :mod:`roborsi.embodied.embodiment.arm.flexiv.session.rdk_adapter`.

Flexiv Rizon is 7-DOF; fake state defaults to zeros for joints and a
plausible resting pose for the TCP. All motions complete instantly and
report Success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Mode(str, Enum):
    IDLE = "IDLE"
    NRT_JOINT_POSITION = "NRT_JOINT_POSITION"
    NRT_CARTESIAN_MOTION_FORCE = "NRT_CARTESIAN_MOTION_FORCE"
    NRT_PRIMITIVE_EXECUTION = "NRT_PRIMITIVE_EXECUTION"


@dataclass
class _RobotStates:
    q: list[float] = field(default_factory=lambda: [0.0] * 7)
    dq: list[float] = field(default_factory=lambda: [0.0] * 7)
    tcp_pose: list[float] = field(
        # Flexiv order: [x, y, z, qw, qx, qy, qz]
        default_factory=lambda: [0.687, 0.0, 0.4, 1.0, 0.0, 0.0, 0.0]
    )
    tau: list[float] = field(default_factory=lambda: [0.0] * 7)
    ext_wrench_in_world: list[float] = field(default_factory=lambda: [0.0] * 6)


class Robot:
    """Fake Robot — records last command, always reports operational."""

    def __init__(self, sn: str) -> None:
        self.sn = sn
        self._mode = Mode.IDLE
        self._states = _RobotStates()
        self._busy = False
        self._fault = False
        self._last_primitive: str = ""
        self._last_primitive_params: dict[str, Any] = {}

    # -- lifecycle --

    def Enable(self) -> None:
        self._fault = False

    def Stop(self) -> None:
        self._busy = False

    def fault(self) -> bool:
        return self._fault

    def ClearFault(self) -> bool:
        self._fault = False
        return True

    def operational(self) -> bool:
        return not self._fault

    def connected(self) -> bool:
        return True

    def busy(self) -> bool:
        return self._busy

    # -- mode --

    def SwitchMode(self, mode: Mode) -> None:
        self._mode = mode

    def mode(self) -> Mode:
        return self._mode

    # -- state --

    def states(self) -> _RobotStates:
        return self._states

    # -- motion --

    def SendJointPosition(
        self,
        positions: list[float],
        velocities: list[float] | None = None,
        accelerations: list[float] | None = None,
        max_vel: list[float] | None = None,
        max_acc: list[float] | None = None,
    ) -> None:
        self._states.q = list(positions)

    def SendCartesianMotionForce(
        self,
        pose: list[float],
        wrench: list[float] | None = None,
        max_linear_vel: float = 0.2,
        max_angular_vel: float = 1.0,
    ) -> None:
        self._states.tcp_pose = list(pose)

    # -- primitives --

    def ExecutePrimitive(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        block_until_started: bool = True,
    ) -> None:
        self._last_primitive = name
        self._last_primitive_params = params or {}

    def primitive_states(self) -> dict[str, str]:
        return {
            "primitive_name": self._last_primitive,
            "reached_target": "true",
        }


class Gripper:
    """Fake Gripper — instantaneous open/close."""

    def __init__(self, robot: Robot) -> None:
        self._robot = robot
        self._width = 0.085

    def Move(self, width: float, velocity: float = 0.1, force_limit: float = 20.0) -> None:
        self._width = float(width)

    def Stop(self) -> None:
        pass

    def states(self) -> dict[str, float]:
        return {"width": self._width, "force": 0.0, "max_width": 0.085}


class Tool:
    """Fake Tool — no-op."""

    def __init__(self, robot: Robot) -> None:
        self._robot = robot

    def name(self) -> str:
        return "default"
