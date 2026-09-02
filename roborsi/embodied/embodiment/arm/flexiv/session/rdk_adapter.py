"""RdkAdapter — wraps flexivrdk with mode-caching and coordinate conversion.

Encapsulates every bit of Flexiv-specific knowledge so handlers can stay
domain-oriented. The two recurring concerns:

1. Mode state machine — only call ``SwitchMode`` when the target mode
   differs from the cached current mode. Avoids ~100ms switches per call.
2. Pose format — Flexiv uses ``[x, y, z, qw, qx, qy, qz]`` while the rest
   of our stack (and most robotics libs) use ``[x, y, z, qx, qy, qz, qw]``.
   Convert at the boundary and keep all sidecar-internal poses in standard
   order.
"""

from __future__ import annotations

import os
from typing import Any


def _load_rdk():
    """Return the flexivrdk module, or the fake one when FAKE=1."""
    if os.environ.get("ROBORSI_FLEXIV_FAKE") == "1":
        from roborsi.embodied.embodiment.arm.flexiv.session import fake_rdk
        return fake_rdk
    import flexivrdk  # noqa: F401 — real RDK is required otherwise
    return flexivrdk


def flexiv_to_standard_pose(pose: list[float]) -> list[float]:
    """[x,y,z,qw,qx,qy,qz]  ->  [x,y,z,qx,qy,qz,qw]."""
    x, y, z, qw, qx, qy, qz = pose
    return [x, y, z, qx, qy, qz, qw]


def standard_to_flexiv_pose(pose: list[float]) -> list[float]:
    """[x,y,z,qx,qy,qz,qw]  ->  [x,y,z,qw,qx,qy,qz]."""
    x, y, z, qx, qy, qz, qw = pose
    return [x, y, z, qw, qx, qy, qz]


class RdkAdapter:
    """Thin, stateful wrapper around ``flexivrdk.Robot`` + ``Gripper``."""

    def __init__(self, sn: str) -> None:
        self._sn = sn
        self._rdk = _load_rdk()
        self._robot = self._rdk.Robot(sn)
        # TODO(hardware): real RDK may raise on Enable if e-stop engaged.
        self._robot.Enable()
        self._gripper = self._rdk.Gripper(self._robot)
        self._gripper_enabled = False
        self._current_mode: str = "IDLE"
        # Pre-bind the gripper while still in IDLE — Tool.Switch rejects other modes.
        self._ensure_gripper()

    def _ensure_gripper(self, name: str = "Robotiq-2F-85", tool_name: str | None = None) -> None:
        """Lazy-enable the gripper by name. No-op after first call.

        Follows SuperInference flow: Gripper.Enable(name) + Tool.Switch(tool_name)
        so the robot's kinematic model includes the EEF. Must be called while
        the robot is in IDLE — Tool.Switch rejects other modes.
        """
        if self._gripper_enabled:
            return
        # Make sure we're in IDLE so Tool.Switch succeeds.
        if self._current_mode != "IDLE":
            self._switch_mode("IDLE")
        try:
            self._gripper.Enable(name)
            tool = self._rdk.Tool(self._robot)
            tool.Switch(tool_name or name)
        except AttributeError:
            # Fake RDK lacks Enable/Tool.
            pass
        self._gripper_enabled = True

    @property
    def sn(self) -> str:
        return self._sn

    # -- mode --

    def _switch_mode(self, mode_name: str) -> None:
        if self._current_mode == mode_name:
            return
        mode_enum = getattr(self._rdk.Mode, mode_name)
        self._robot.SwitchMode(mode_enum)
        self._current_mode = mode_name

    def current_mode(self) -> str:
        return self._current_mode

    # -- state --

    def read_state(self) -> dict[str, Any]:
        s = self._robot.states()
        tcp_standard = flexiv_to_standard_pose(list(s.tcp_pose))
        return {
            "q": list(s.q),
            "dq": list(s.dq),
            "tau": list(s.tau),
            "tcp_pose": tcp_standard,
            "ext_wrench": list(s.ext_wrench_in_world),
            "mode": self._current_mode,
            "operational": bool(self._robot.operational()),
            "fault": bool(self._robot.fault()),
        }

    # -- motion --

    def move_joint(self, q: list[float], max_vel: float, max_acc: float) -> None:
        # RDK v1.8 signature: (q, q_dot, q_ddot, max_vels) ... 4 lists.
        self._switch_mode("NRT_JOINT_POSITION")
        max_vels = [max_vel] * len(q)
        max_accs = [max_acc] * len(q)
        self._robot.SendJointPosition(q, max_vels, max_accs, max_vels)

    def move_tcp(self, standard_pose: list[float], max_linear_vel: float) -> None:
        # TODO(hardware): confirm SendCartesianMotionForce accepts 7-float pose only.
        self._switch_mode("NRT_CARTESIAN_MOTION_FORCE")
        flexiv_pose = standard_to_flexiv_pose(standard_pose)
        self._robot.SendCartesianMotionForce(flexiv_pose, max_linear_vel=max_linear_vel)

    def stop(self) -> None:
        self._robot.Stop()
        self._current_mode = "IDLE"

    def busy(self) -> bool:
        return bool(self._robot.busy())

    # -- primitives --

    def run_primitive(self, name: str, params: dict[str, Any]) -> None:
        # TODO(hardware): params dict may need string serialization per RDK API.
        self._switch_mode("NRT_PRIMITIVE_EXECUTION")
        self._robot.ExecutePrimitive(name, params, block_until_started=True)

    def primitive_state(self) -> dict[str, Any]:
        raw = self._robot.primitive_states()
        if isinstance(raw, dict):
            return raw
        # real RDK returns a list of "key=value" strings; normalize.
        out: dict[str, Any] = {}
        for line in raw:
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    # -- gripper --

    def gripper_move(self, width: float, velocity: float = 0.1, force: float = 100.0) -> None:
        self._ensure_gripper()
        self._gripper.Move(width, velocity, force)

    def gripper_state(self) -> dict[str, Any]:
        self._ensure_gripper()
        s = self._gripper.states()
        if isinstance(s, dict):
            return s
        return {"width": float(s.width), "force": float(getattr(s, "force", 0.0))}

    # -- teardown --

    def shutdown(self) -> None:
        self._robot.Stop()
