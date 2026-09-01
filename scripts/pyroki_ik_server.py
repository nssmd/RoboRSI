"""PyRoKi inverse-kinematics + trajectory-optimization ZMQ service (isolated
``pyroki`` conda env).

PyRoKi's exact least-squares IK reaches ANY reachable pose and never wedges at
kinematic limits — unlike the per-step Jacobian/OSC servo, which froze mid-move
at joint limits (capping LIBERO success at ~8%). But IK returns only the GOAL
config; driving to it straight-line in joint space can still plow the arm
through the table or into itself and HARD-FREEZE at a high pose. So this service
also offers COLLISION-FREE TRAJECTORY OPTIMIZATION (``op=trajopt``): a smooth
joint path that keeps every swept-arm segment clear of the table (a HalfSpace)
and of itself, so the driven waypoints escape / avoid jams.

PyRoKi needs ``jax`` (numpy<2), which conflicts with the numpy-2 eval env, so it
runs here as a standalone service, exactly like the GraspGen / SAM3 ZMQ services
this repo already uses.

The client (``roborsi.embodied.skills.base._lib.libero._control``) sends
``panda_hand`` target pose(s) IN THE PYROKI ROBOT'S OWN BASE FRAME and gets back
arm joint angles. Frame alignment (world ↔ pyroki-base) is the CLIENT's job (see
``_control._goal_config``) — this service is a pure solver, frame-agnostic.

Wire format: raw ZMQ REP + pickle (matching ``_perception.locate_by_sam3``).
Requests are dispatched by ``op``:
  ``{"op": "ik", "target_wxyz", "target_pos", "current_joints"}``
  → ``{"joints": [q1..q7]}``
  ``{"op": "trajopt", "start_wxyz", "start_pos", "end_wxyz", "end_pos",
     "start_joints", "timesteps"}``  → ``{"traj": [[q1..q7], ...]}``

Launch (in the pyroki env)::

    ROBORSI_PYROKI_PORT=5559 \
      ~/miniconda3/envs/pyroki/bin/python scripts/pyroki_ik_server.py

Both JITs are warmed on startup (first solve compiles ~seconds; warm IK ~1 ms,
warm trajopt ~tens of ms).
"""

from __future__ import annotations

import os
import pickle

import numpy as np
import pyroki as pk
import zmq
from pyroki_protocol import (
    WIRE_PROTOCOL,
    trajectory_start_error,
    validate_arm_joints,
)
from robot_descriptions.loaders.yourdfpy import load_robot_description
from solve_ik import solve_ik
from solve_trajopt import solve_joint_trajopt, solve_trajopt

_TARGET_LINK = "panda_hand"
# The client (`_control._TRAJOPT_TIMESTEPS`) requests 10 timesteps; warm THAT count
# so the first production trajopt is fast (the jitted graph is per-timestep, and a
# mismatched warm-up would force a ~7 s recompile on the first real request).
_TRAJ_TIMESTEPS = 10     # client default; requests may override

# The LIBERO table top sits at world z=0.90; the pyroki base mounts at world
# z=0.912 with identity rotation, so in the base frame the table plane is at
# z=-0.012. Model it as an up-facing HalfSpace (the ground). Self-collision +
# this plane is enough to keep the swept arm off the table and out of itself.
_TABLE_Z_IN_BASE = -0.012


class Solver:
    """Holds the loaded robot + collision geometry (built once) and answers IK
    and trajopt requests."""

    def __init__(self) -> None:
        urdf = load_robot_description("panda_description")
        self.robot = pk.Robot.from_urdf(urdf)
        self.robot_coll = pk.collision.RobotCollision.from_urdf(urdf)
        table = pk.collision.HalfSpace.from_point_and_normal(
            np.array([0.0, 0.0, _TABLE_Z_IN_BASE]), np.array([0.0, 0.0, 1.0]))
        self.world_coll = [table]

    def _full_config(self, arm_joints) -> np.ndarray:
        config = np.asarray(
            self.robot.joint_var_cls(0).default_factory(),
            dtype=np.float64,
        ).copy()
        config[:7] = np.asarray(arm_joints, dtype=np.float64)
        return config

    def solve_ik(self, target_wxyz, target_pos, current_joints) -> list[float]:
        """One IK solve → the 7 arm joint angles (drop the finger joint)."""
        cfg = solve_ik(
            self.robot, _TARGET_LINK,
            np.asarray(target_wxyz, dtype=np.float64),
            np.asarray(target_pos, dtype=np.float64),
            initial_cfg=self._full_config(current_joints),
        )
        return [float(q) for q in cfg[:7]]

    def solve_trajopt(self, start_wxyz, start_pos, end_wxyz, end_pos,
                      start_joints, timesteps) -> tuple[list[list[float]], float]:
        """Collision-free joint trajectory → list of per-timestep 7-arm configs."""
        traj = solve_trajopt(
            self.robot, self.robot_coll, self.world_coll, _TARGET_LINK,
            np.asarray(start_pos, dtype=np.float64),
            np.asarray(start_wxyz, dtype=np.float64),
            np.asarray(end_pos, dtype=np.float64),
            np.asarray(end_wxyz, dtype=np.float64),
            int(timesteps),
            start_cfg=self._full_config(start_joints),
        )
        arm_traj = [[float(q) for q in cfg[:7]] for cfg in traj]
        return arm_traj, trajectory_start_error(arm_traj, start_joints)

    def solve_joint_trajopt(
        self,
        start_joints,
        end_joints,
        timesteps,
    ) -> tuple[list[list[float]], float, float]:
        traj = solve_joint_trajopt(
            self.robot,
            self.robot_coll,
            self.world_coll,
            self._full_config(start_joints),
            self._full_config(end_joints),
            int(timesteps),
        )
        arm_traj = [[float(q) for q in cfg[:7]] for cfg in traj]
        return (
            arm_traj,
            trajectory_start_error(arm_traj, start_joints),
            trajectory_start_error(reversed(arm_traj), end_joints),
        )

    def handle(self, req: dict) -> dict:
        """Dispatch one solver request."""
        if req.get("protocol") != WIRE_PROTOCOL:
            raise ValueError("PyRoKi wire protocol mismatch")
        op = req.get("op")
        if op == "joint_trajopt":
            start_joints = validate_arm_joints(
                req.get("start_joints"), field="start_joints"
            )
            end_joints = validate_arm_joints(
                req.get("end_joints"), field="end_joints"
            )
            traj, start_error, end_error = self.solve_joint_trajopt(
                start_joints,
                end_joints,
                req.get("timesteps", _TRAJ_TIMESTEPS),
            )
            return {
                "protocol": WIRE_PROTOCOL,
                "traj": traj,
                "start_error_max": start_error,
                "end_error_max": end_error,
            }
        if op == "trajopt":
            start_joints = validate_arm_joints(
                req.get("start_joints"), field="start_joints"
            )
            traj, start_error = self.solve_trajopt(
                req["start_wxyz"], req["start_pos"], req["end_wxyz"],
                req["end_pos"], start_joints,
                req.get("timesteps", _TRAJ_TIMESTEPS))
            return {
                "protocol": WIRE_PROTOCOL,
                "traj": traj,
                "start_error_max": start_error,
            }
        if op == "ik":
            current_joints = validate_arm_joints(
                req.get("current_joints"), field="current_joints"
            )
            return {
                "protocol": WIRE_PROTOCOL,
                "joints": self.solve_ik(
                    req["target_wxyz"], req["target_pos"], current_joints
                ),
            }
        raise ValueError(f"unsupported PyRoKi operation: {op}")


def _warm(solver: Solver) -> None:
    """Compile both jitted problems before binding so the first real request is
    already fast (IK ~1 ms, trajopt ~tens of ms warm)."""
    print("[pyroki-ik] warming IK JIT…", flush=True)
    ready = [0.0, 0.0, 0.0, -1.5708, 0.0, 1.8675, 0.0]
    solver.solve_ik([1.0, 0.0, 0.0, 0.0], [0.4, 0.0, 0.5], ready)
    print("[pyroki-ik] warming trajopt JIT…", flush=True)
    solver.solve_trajopt([1.0, 0.0, 0.0, 0.0], [0.4, 0.0, 0.5],
                         [1.0, 0.0, 0.0, 0.0], [0.4, 0.2, 0.3], ready,
                         _TRAJ_TIMESTEPS)
    target = [0.1, -0.1, 0.1, -1.7, 0.1, 1.7, 0.1]
    solver.solve_joint_trajopt(ready, target, _TRAJ_TIMESTEPS)
    print("[pyroki-ik] JITs warm.", flush=True)


def main() -> None:
    port = int(os.environ.get("ROBORSI_PYROKI_PORT", "5559"))
    print(f"[pyroki-ik] loading Panda robot ('{_TARGET_LINK}') + collision…",
          flush=True)
    solver = Solver()
    _warm(solver)

    sock = zmq.Context.instance().socket(zmq.REP)
    sock.bind(f"tcp://*:{port}")
    print(f"[pyroki-ik] serving on tcp://*:{port}", flush=True)

    while True:
        req = pickle.loads(sock.recv())
        try:
            response = solver.handle(req)
        except Exception as exc:  # Keep REP state healthy on malformed input.
            response = {
                "protocol": WIRE_PROTOCOL,
                "error": f"{type(exc).__name__}: {exc}",
            }
        sock.send(pickle.dumps(response))


if __name__ == "__main__":
    main()
