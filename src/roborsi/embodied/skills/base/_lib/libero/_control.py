"""Joint-space inverse-kinematics servo for the base/libero skills.

LIBERO's Franka is driven through robosuite's JOINT_POSITION controller: an
8-D action ``[dq1..dq7, gripper]`` (arm joint deltas scaled to ~``JOINT_STEP``
rad at |cmd|=1; gripper ``+1`` close / ``-1`` open). Reaching a world EE pose is
a WHOLE-ARM inverse-kinematics servo: a PyRoKi least-squares solver (running in
an isolated conda env, reached over ZMQ — see ``scripts/pyroki_ik_server.py``)
returns the 7 arm joints that place the EE at the target, and this servo drives
the JOINT_POSITION controller monotonically to that config (``dq = q_goal -
q_cur`` clipped by ``JOINT_STEP``). Because the goal config is a full IK solution
(reachable, joint-limit-respecting), the arm cannot WEDGE at kinematic limits.

But a reachable goal config says nothing about the PATH to it: driving straight-
line in joint space can still sweep the arm through the table or into itself and
HARD-FREEZE at a high pose (measured: frozen at z≈1.37, wedged, every command a
no-op). So when the monotonic drive DETECTS a freeze (joints unchanged mid-move),
it escapes with a COLLISION-FREE joint TRAJECTORY (``op=trajopt`` — avoiding self-
collision + the table) and streams through its waypoints. Trajopt (~50 ms warm vs
~1 ms IK) is paid ONLY on a wedge, so the common move stays fast.

This replaces the per-step Jacobian/OSC servo, which froze at kinematic limits
(the arm stopped mid-move — e.g. at z≈1.4 on a back+up reach — while commanding
full effort, so grasps mis-seated and places dropped short). That Jacobian servo
survives only as a FALLBACK, used when the PyRoKi service is unreachable.

Frame alignment (world grip_site ↔ pyroki-base panda_hand), calibrated by
round-trip (recover the live arm config within ~0.01 rad):
  1. hand_pos_world = grip_pos + R_grip @ TCP_OFFSET   (grip_site → panda_hand)
  2. pos_in_base    = hand_pos_world - BASE_WORLD       (base rotation is identity)
  3. wxyz_in_base   = xyzw→wxyz(grip_quat)              (grip & hand share orient.)

Plain helper module (no SKILL.md → not a tool). Each skill builds
``LiberoControl(state.env)`` and calls its methods. Poses are read from the
LiberoProEnv's cached ``raw_obs()`` (refreshed by every ``env.step``); the
Jacobian fallback reads the live MuJoCo data, which is current post-step.
"""

from __future__ import annotations

import os
import pickle
from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero.gripper_state import (
    GripperCalibration,
    GripperClassifier,
    GripperState,
)
from roborsi.embodied.skills.base._lib.libero.visual_hold import (
    clear_visual_hold,
)
from scripts.pyroki_protocol import WIRE_PROTOCOL as _PYROKI_WIRE_PROTOCOL

JOINT_STEP = 0.35        # JOINT_POSITION output range at |cmd|=1 (rad)
GRIP_CLOSE = 1.0
GRIP_OPEN = -1.0
_DAMP = 0.04             # damped-least-squares λ (Jacobian fallback)
_MAX_ERR = 0.12          # cap per-step cartesian error fed to Jacobian IK

# ── PyRoKi frame-alignment constants (calibrated by round-trip) ──────────────
# Fallback only. The active LIBERO-PRO scene reports its robot base at runtime.
_BASE_WORLD = np.array([-0.66, 0.0, 0.912])
# grip_site sits ~0.097 m BELOW panda_hand along the hand's local +z (approach)
# axis, so panda_hand = grip_site backed off along -z_grip. Sign verified by
# round-trip: −0.097 recovers the live arm config within ~0.01 rad (+0.097 → 0.5).
_TCP_OFFSET = np.array([0.0, 0.0, -0.097])
_IK_TOL = 0.02           # per-joint tolerance to call the goal config reached (rad)
# The JOINT_POSITION controller advances only ~0.008 rad/step even at a saturated
# command (its output_max, NOT ``JOINT_STEP``), so a full-arm reconfiguration of
# ~1.3 rad needs ~180 steps. The goal config is a fixed, reachable IK solution, so
# driving to it can't wedge; we size the step budget from the joint distance
# (+margin) and stop the instant it's reached — the skills' ``max_iters`` (tuned
# for the old cartesian Jacobian servo) is far too small for this joint traverse.
_CTRL_RATE = 0.008       # measured effective joint motion per saturated step (rad)
_CFG_ITER_MARGIN = 1.6   # slack over the ideal step count (settling / coupling)
_CFG_ITER_MIN = 60       # floor for short moves
_CFG_ITER_MAX = 400      # ceiling (prevents a runaway if a joint truly can't move)
_JOINT_RECOVERY_TOL = 0.03
_JOINT_RECOVERY_TRAJOPT_THRESHOLD = 0.35
_JOINT_RECOVERY_WAYPOINT_TOL = 0.08
_JOINT_RECOVERY_WAYPOINT_STEPS = 16

# ── Collision-free trajectory optimization (wedge escape) ────────────────────
# A plain IK solve gives a reachable GOAL config, but driving to it straight-line
# in joint space can sweep the arm through the table / into itself and hard-freeze
# at a high pose. When ``_servo_to_config`` detects the arm has FROZEN mid-drive
# (joints unchanged while still far from the goal), we ask the service for a
# collision-free joint TRAJECTORY (avoiding self-collision + the table HalfSpace)
# and STREAM through its waypoints — the collision-free property is in the path
# SHAPE, so we advance to the next waypoint as soon as the arm is loosely near the
# current one (no full settle), then snap onto the target with one tight plain-IK
# drive. Trajopt (~50 ms warm) is paid ONLY on a wedge, so the common path is fast.
_TRAJOPT_TIMESTEPS = 10    # waypoints per planned trajectory
_TRAJOPT_ADVANCE = 0.20    # per-joint tol (rad) to advance to the next waypoint — loose: pass THROUGH intermediate waypoints, only the final IK snap settles tight
_TRAJOPT_SETTLE = 8        # step cap per waypoint — the LIBERO horizon is 1000 physics steps, so a trajopt move must cost ~135 steps (10×settle), not ~470
_PREVIEW_START_TOL = 0.03
_OSC_TRANSLATION_SCALE = 0.05
_OSC_ROTATION_SCALE = 0.5
_OSC_STALL_STEPS = 12


def bounded_residual_correction_target(
    target: Any,
    measured: Any,
    *,
    max_total_error: float,
    max_xy_error: float,
    max_z_error: float,
    max_move: float,
) -> np.ndarray | None:
    target_point = np.asarray(target, dtype=float)
    measured_point = np.asarray(measured, dtype=float)
    if (
        target_point.shape != (3,)
        or measured_point.shape != (3,)
        or not np.all(np.isfinite(target_point))
        or not np.all(np.isfinite(measured_point))
    ):
        return None
    residual = target_point - measured_point
    total_error = float(np.linalg.norm(residual))
    xy_error = float(np.linalg.norm(residual[:2]))
    z_error = abs(float(residual[2]))
    if (
        total_error <= 0.0
        or total_error > float(max_total_error)
        or xy_error > float(max_xy_error)
        or z_error > float(max_z_error)
        or z_error > float(max_move)
    ):
        return None
    desired_xy = 2.0 * residual[:2]
    xy_budget = float(
        np.sqrt(max(0.0, float(max_move) ** 2 - residual[2] ** 2))
    )
    desired_norm = float(np.linalg.norm(desired_xy))
    if desired_norm > xy_budget:
        desired_xy *= xy_budget / desired_norm
    corrected = measured_point.copy()
    corrected[:2] += desired_xy
    corrected[2] = target_point[2]
    return corrected


def _calibration_from_model(model, rs) -> GripperCalibration:
    joints = list(getattr(getattr(rs.robots[0], "gripper", None), "joints", []) or [])
    if len(joints) >= 2:
        try:
            left_id = model.joint(joints[0]).id
            right_id = model.joint(joints[1]).id
            left = tuple(float(v) for v in model.jnt_range[left_id])
            right = tuple(float(v) for v in model.jnt_range[right_id])
            return GripperCalibration.from_joint_ranges(left, right)
        except Exception:  # noqa: BLE001
            pass
    # Conservative fallback when joint ranges are unavailable.
    return GripperCalibration.from_joint_ranges((0.0, 0.04), (-0.04, 0.0))


def _axisangle_err(q_cur: np.ndarray, q_tgt: np.ndarray) -> np.ndarray:
    """Axis-angle vector (rad, world frame) rotating q_cur → q_tgt.
    robosuite quaternions are [x, y, z, w]."""
    from robosuite.utils import transform_utils

    q_err = transform_utils.quat_multiply(
        q_tgt,
        transform_utils.quat_inverse(q_cur),
    )
    return np.asarray(transform_utils.quat2axisangle(q_err), dtype=float)


def _solve_ik_zmq(
    wxyz: np.ndarray,
    pos_in_base: np.ndarray,
    current_joints: np.ndarray,
) -> np.ndarray | None:
    """Ask the PyRoKi service for the 7 arm joints placing panda_hand at the
    given pose IN THE PYROKI BASE FRAME. Returns None if the service is off
    (``ROBORSI_PYROKI_PORT`` unset) or unreachable, so the caller can fall
    back to the Jacobian servo."""
    resp = _request(
        {
            "protocol": _PYROKI_WIRE_PROTOCOL,
            "op": "ik",
            "target_wxyz": [float(x) for x in wxyz],
            "target_pos": [float(x) for x in pos_in_base],
            "current_joints": [float(x) for x in current_joints],
        }
    )
    if resp is None or resp.get("protocol") != _PYROKI_WIRE_PROTOCOL:
        return None
    joints = np.asarray(resp.get("joints"), dtype=float)
    return joints if joints.shape == (7,) and np.all(np.isfinite(joints)) else None


def _trajopt_zmq(
    start_wxyz,
    start_pos,
    end_wxyz,
    end_pos,
    start_joints,
    timesteps: int,
) -> np.ndarray | None:
    """Ask the PyRoKi service for a COLLISION-FREE joint trajectory (avoiding
    self-collision + the table) between two panda_hand poses IN THE PYROKI BASE
    FRAME. Returns a ``(timesteps, 7)`` array, or None if the service is off /
    unreachable so the caller can fall back to a plain IK solve."""
    resp = _request({"protocol": _PYROKI_WIRE_PROTOCOL,
                     "op": "trajopt",
                     "start_wxyz": [float(x) for x in start_wxyz],
                     "start_pos": [float(x) for x in start_pos],
                     "end_wxyz": [float(x) for x in end_wxyz],
                     "end_pos": [float(x) for x in end_pos],
                     "start_joints": [float(x) for x in start_joints],
                     "timesteps": int(timesteps)})
    if resp is None or resp.get("protocol") != _PYROKI_WIRE_PROTOCOL:
        return None
    try:
        start_error = float(resp.get("start_error_max"))
    except (TypeError, ValueError, OverflowError):
        return None
    traj = np.asarray(resp.get("traj"), dtype=float)
    if (
        not np.isfinite(start_error)
        or start_error > 1e-4
        or traj.ndim != 2
        or traj.shape[1] != 7
        or not np.all(np.isfinite(traj))
    ):
        return None
    return traj


def _joint_trajopt_zmq(
    start_joints: np.ndarray,
    end_joints: np.ndarray,
    timesteps: int,
) -> np.ndarray | None:
    resp = _request(
        {
            "protocol": _PYROKI_WIRE_PROTOCOL,
            "op": "joint_trajopt",
            "start_joints": [float(x) for x in start_joints],
            "end_joints": [float(x) for x in end_joints],
            "timesteps": int(timesteps),
        }
    )
    if resp is None or resp.get("protocol") != _PYROKI_WIRE_PROTOCOL:
        return None
    try:
        start_error = float(resp.get("start_error_max"))
        end_error = float(resp.get("end_error_max"))
    except (TypeError, ValueError, OverflowError):
        return None
    trajectory = np.asarray(resp.get("traj"), dtype=float)
    if (
        not np.isfinite(start_error)
        or not np.isfinite(end_error)
        or start_error > 1e-4
        or end_error > 1e-4
        or trajectory.ndim != 2
        or trajectory.shape[1] != 7
        or not np.all(np.isfinite(trajectory))
    ):
        return None
    return trajectory


def _request(payload: dict) -> dict | None:
    """One PyRoKi ZMQ request/response, or None if the service is off (port
    unset) or unreachable. Shared by the IK and trajopt clients."""
    port = int(os.environ.get("ROBORSI_PYROKI_PORT", "0"))
    if not port:
        return None
    import zmq
    sock = zmq.Context.instance().socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 30000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://localhost:{port}")
    try:
        sock.send(pickle.dumps(payload))
        return pickle.loads(sock.recv())
    except zmq.error.ZMQError:
        return None                       # service down → caller falls back
    finally:
        sock.close()


class LiberoControl:
    """Whole-arm IK servo wrapper over a ``LiberoProEnv`` (JOINT_POSITION).

    ``servo_to`` solves for a goal joint config with PyRoKi (over ZMQ) and drives
    to it monotonically; if the service is unreachable it falls back to the
    per-step Jacobian IK."""

    def __init__(self, env: Any) -> None:
        self.env = env
        try:
            base_world = np.asarray(env.robot_base_pos(), dtype=float)
        except (AttributeError, TypeError, ValueError):
            base_world = _BASE_WORLD.copy()
        self._base_world = (
            base_world
            if base_world.shape == (3,) and np.all(np.isfinite(base_world))
            else _BASE_WORLD.copy()
        )
        rs = env._env.env                          # robosuite env under the adapter
        self._model = rs.sim.model._model
        self._data = rs.sim.data._data
        self._site = self._model.site("gripper0_grip_site").id
        self._arm = np.asarray(rs.robots[0]._ref_joint_vel_indexes)   # Jacobian columns
        self._adim = int(rs.action_dim)            # 8 for JOINT_POSITION (7 + gripper)
        self._is_osc = self._adim == 7
        ready = np.asarray(
            getattr(env, "_libero_ready_joint_qpos", []),
            dtype=float,
        )
        if ready.shape != (7,) or not np.all(np.isfinite(ready)):
            env._libero_ready_joint_qpos = self._arm_qpos().copy()
        ready_pose = getattr(env, "_libero_ready_ee_pose", None)
        if not (
            isinstance(ready_pose, tuple)
            and len(ready_pose) == 2
            and np.asarray(ready_pose[0]).shape == (3,)
            and np.asarray(ready_pose[1]).shape == (4,)
        ):
            ready_pos, ready_quat, _ = self.read_pose()
            env._libero_ready_ee_pose = (
                np.asarray(ready_pos, dtype=float).copy(),
                np.asarray(ready_quat, dtype=float).copy(),
            )
        if not isinstance(getattr(env, "_libero_gripper_classifier", None), GripperClassifier):
            env._libero_gripper_classifier = GripperClassifier(
                _calibration_from_model(self._model, rs)
            )
        if not isinstance(getattr(env, "_libero_last_gripper_command", None), str):
            env._libero_last_gripper_command = None

    # ── ground-truth reads ───────────────────────────────────────────────
    def read_pose(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(eef_pos[3], eef_quat[4] xyzw, gripper_qpos[2]) from the last obs."""
        obs = self.env.raw_obs()
        return (np.asarray(obs["robot0_eef_pos"], dtype=float),
                np.asarray(obs["robot0_eef_quat"], dtype=float),
                np.asarray(obs["robot0_gripper_qpos"], dtype=float))

    def _arm_qpos(self) -> np.ndarray:
        """The 7 current arm joint angles (rad) from the last obs."""
        return np.asarray(self.env.raw_obs()["robot0_joint_pos"], dtype=float)[:7]

    def _gripper_gap(self, gq: np.ndarray | None = None) -> float:
        pose = gq
        if pose is None:
            _, _, pose = self.read_pose()
        return float(pose[0] - pose[1])

    def _gripper_classifier(self) -> GripperClassifier:
        return self.env._libero_gripper_classifier

    def _set_last_gripper_command(self, command: str | None) -> None:
        self.env._libero_last_gripper_command = command

    def read_gripper_state(self) -> tuple[float, GripperState]:
        gap = self._gripper_gap()
        state = self._gripper_classifier().classify(
            gap,
            last_command=self.env._libero_last_gripper_command,
        )
        if state is not GripperState.HELD:
            clear_visual_hold(self.env)
        return gap, state

    def is_open(self) -> bool:
        _, state = self.read_gripper_state()
        return state is GripperState.OPEN

    def _hold_grip(self) -> float:
        """The gripper command that KEEPS the current open/closed state."""
        return GRIP_OPEN if self.is_open() else GRIP_CLOSE

    def joint_posture_error(self) -> float:
        target = np.asarray(
            getattr(self.env, "_libero_ready_joint_qpos", []),
            dtype=float,
        )
        if target.shape != (7,) or not np.all(np.isfinite(target)):
            return float("inf")
        return float(np.max(np.abs(target - self._arm_qpos())))

    def _follow_recovery_trajectory(
        self,
        trajectory: np.ndarray,
        *,
        remaining: int,
        grip: float,
        waypoint_tol: float,
    ):
        last = None
        for waypoint in np.asarray(trajectory, dtype=float)[1:]:
            for _ in range(
                min(_JOINT_RECOVERY_WAYPOINT_STEPS, remaining)
            ):
                delta = waypoint - self._arm_qpos()
                if float(np.max(np.abs(delta))) <= waypoint_tol:
                    break
                last = self._step(delta, grip)
                remaining -= 1
                if remaining <= 0 or (last is not None and last.done):
                    break
            if remaining <= 0 or (last is not None and last.done):
                break
        return last, remaining

    def recover_ready_posture(self, *, max_iters: int = 240):
        """Drive directly toward the episode's captured ready joint posture."""
        if getattr(self, "_is_osc", False):
            ready = getattr(self.env, "_libero_ready_ee_pose", None)
            if not isinstance(ready, tuple) or len(ready) != 2:
                return False, None
            return self._servo_osc(
                np.asarray(ready[0], dtype=float),
                np.asarray(ready[1], dtype=float),
                self._hold_grip(),
                0.02,
                0.12,
                max_iters,
            )
        target = np.asarray(
            getattr(self.env, "_libero_ready_joint_qpos", []),
            dtype=float,
        )
        if target.shape != (7,) or not np.all(np.isfinite(target)):
            return False, None
        last = None
        remaining = max(1, min(int(max_iters), _CFG_ITER_MAX))
        current = self._arm_qpos()
        if float(np.max(np.abs(target - current))) >= _JOINT_RECOVERY_TRAJOPT_THRESHOLD:
            trajectory = _joint_trajopt_zmq(
                current,
                target,
                _TRAJOPT_TIMESTEPS,
            )
            if trajectory is not None:
                grip = self._hold_grip()
                last, remaining = self._follow_recovery_trajectory(
                    trajectory,
                    remaining=remaining,
                    grip=grip,
                    waypoint_tol=_JOINT_RECOVERY_WAYPOINT_TOL,
                )
        previous = None
        frozen = 0
        while remaining > 0:
            current = self._arm_qpos()
            delta = target - current
            if float(np.max(np.abs(delta))) <= _JOINT_RECOVERY_TOL:
                return True, last
            if previous is not None and float(np.max(np.abs(current - previous))) < 1e-4:
                frozen += 1
                if frozen >= 12:
                    break
            else:
                frozen = 0
            previous = current.copy()
            last = self._step(delta, self._hold_grip())
            remaining -= 1
            if last is not None and last.done:
                break
        if frozen >= 12 and remaining > 0:
            trajectory = _joint_trajopt_zmq(
                self._arm_qpos(),
                target,
                _TRAJOPT_TIMESTEPS,
            )
            if trajectory is not None:
                fallback_last, remaining = self._follow_recovery_trajectory(
                    trajectory,
                    remaining=remaining,
                    grip=self._hold_grip(),
                    waypoint_tol=_JOINT_RECOVERY_TOL,
                )
                if fallback_last is not None:
                    last = fallback_last
        return self.joint_posture_error() <= _JOINT_RECOVERY_TOL, last

    # ── primitive step ───────────────────────────────────────────────────
    def _step(self, dq: np.ndarray, grip: float):
        if getattr(self, "_is_osc", False):
            if float(np.max(np.abs(np.asarray(dq, dtype=float)))) > 1e-8:
                raise RuntimeError(
                    "joint delta is unavailable under OSC_POSE control"
                )
            return self._osc_step(np.zeros(6, dtype=float), grip)
        action = np.zeros(self._adim, dtype=float)
        action[:7] = np.clip(dq / JOINT_STEP, -1.0, 1.0)
        action[-1] = grip
        if grip < 0:
            clear_visual_hold(self.env)
        step = self.env.step(action)     # LiberoProEnv.step → Step; refreshes raw_obs
        gap = self._gripper_gap()
        if grip > 0:
            self._set_last_gripper_command("close")
            self._gripper_classifier().on_keep_close(gap=gap)
        elif grip < 0:
            self._set_last_gripper_command("open")
            self._gripper_classifier().on_keep_open(gap=gap)
        return step

    def _osc_step(self, command: np.ndarray, grip: float):
        action = np.zeros(self._adim, dtype=float)
        action[:6] = np.clip(np.asarray(command, dtype=float), -1.0, 1.0)
        action[-1] = grip
        if grip < 0:
            clear_visual_hold(self.env)
        step = self.env.step(action)
        gap = self._gripper_gap()
        if grip > 0:
            self._set_last_gripper_command("close")
            self._gripper_classifier().on_keep_close(gap=gap)
        elif grip < 0:
            self._set_last_gripper_command("open")
            self._gripper_classifier().on_keep_open(gap=gap)
        return step

    def _ik_dq(self, perr: np.ndarray, rerr: np.ndarray, use_rot: bool) -> np.ndarray:
        """Damped least-squares joint delta for a cartesian EE error, from the
        live MuJoCo site Jacobian (6-DoF when a target orientation is given).
        Fallback path only — used when the PyRoKi service is unreachable."""
        import mujoco

        jacp = np.zeros((3, self._model.nv))
        jacr = np.zeros((3, self._model.nv))
        mujoco.mj_jacSite(self._model, self._data, jacp, jacr, self._site)
        if use_rot:
            jacobian = np.vstack(
                [jacp[:, self._arm], jacr[:, self._arm]]
            )
            err = np.concatenate([perr, rerr])
        else:
            jacobian = jacp[:, self._arm]
            err = perr
        return jacobian.T @ np.linalg.solve(
            jacobian @ jacobian.T + _DAMP * np.eye(len(err)),
            err,
        )

    # ── PyRoKi goal-config solve ──────────────────────────────────────────
    def _pose_in_base(self, pos: np.ndarray, quat_xyzw: np.ndarray | None):
        """Map a world grip_site pose to a panda_hand pose in the pyroki base
        frame: ``(wxyz, pos_in_base)``. Applies the calibrated world↔pyroki-base
        transform (grip_site → panda_hand back-off, then base translation; the
        base rotation is identity). When ``quat`` is None, hold the CURRENT EE
        orientation (the solver needs a full 6-DoF target). Shared by the IK and
        trajopt clients so both use the exact same frame alignment."""
        cur_pos, cur_quat, _ = self.read_pose()
        quat = cur_quat if quat_xyzw is None else np.asarray(quat_xyzw, dtype=float)
        from robosuite.utils import transform_utils

        r_grip = np.asarray(transform_utils.quat2mat(quat))
        hand_pos_world = np.asarray(pos, dtype=float) + r_grip @ _TCP_OFFSET
        pos_in_base = hand_pos_world - np.asarray(
            self._base_world,
            dtype=float,
        )
        wxyz = np.array([quat[3], quat[0], quat[1], quat[2]])   # xyzw → wxyz
        return wxyz, pos_in_base

    def _goal_config(self, pos: np.ndarray, quat_xyzw: np.ndarray | None):
        """The 7 arm joints placing the EE at world grip_site pose ``(pos, quat)``,
        via PyRoKi over ZMQ. Returns None if the service is off."""
        wxyz, pos_in_base = self._pose_in_base(pos, quat_xyzw)
        return _solve_ik_zmq(wxyz, pos_in_base, self._arm_qpos())

    def preview_goal_config(
        self,
        pos: np.ndarray,
        quat_xyzw: np.ndarray | None,
    ) -> np.ndarray | None:
        """Check IK reachability without stepping or changing robot state."""
        goal = self._goal_config(
            np.asarray(pos, dtype=float),
            None if quat_xyzw is None else np.asarray(quat_xyzw, dtype=float),
        )
        return None if goal is None else np.asarray(goal, dtype=float).copy()

    def preview_trajectory(
        self,
        pos: np.ndarray,
        quat_xyzw: np.ndarray | None,
    ) -> np.ndarray | None:
        """Plan a branch-continuous path without stepping the simulator."""
        if getattr(self, "_is_osc", False):
            return None
        trajectory = self._plan_trajopt(
            np.asarray(pos, dtype=float),
            None if quat_xyzw is None else np.asarray(quat_xyzw, dtype=float),
        )
        return None if trajectory is None else np.asarray(trajectory, dtype=float).copy()

    def execute_previewed_trajectory(
        self,
        trajectory: Any,
        *,
        pos: np.ndarray,
        quat: np.ndarray | None,
        gripper: str,
        pos_tol: float = 0.015,
        rot_tol: float = 0.10,
    ):
        """Commit an exact previewed path if its live joint start still matches."""
        if getattr(self, "_is_osc", False):
            return False, None
        try:
            path = np.asarray(trajectory, dtype=float)
        except (TypeError, ValueError, OverflowError):
            return False, None
        if (
            path.ndim != 2
            or path.shape[0] < 2
            or path.shape[1] != 7
            or not np.all(np.isfinite(path))
            or float(np.max(np.abs(path[0] - self._arm_qpos())))
            > _PREVIEW_START_TOL
        ):
            return False, None
        grip = {"open": GRIP_OPEN, "close": GRIP_CLOSE, "keep": None}.get(gripper)
        if gripper not in {"open", "close", "keep"}:
            return False, None
        if grip == GRIP_OPEN:
            clear_visual_hold(self.env)
        target = np.asarray(pos, dtype=float)
        target_quat = None if quat is None else np.asarray(quat, dtype=float)
        return self._servo_via_trajopt(
            path,
            grip,
            target,
            target_quat,
            float(pos_tol),
            float(rot_tol),
        )

    # ── composed motions ─────────────────────────────────────────────────
    def set_gripper(self, close: bool, steps: int = 12):
        """Command open/close and hold (zero joint delta) for ``steps`` ticks so
        the fingers finish moving. Returns the last Step; stops early on done."""
        grip = GRIP_CLOSE if close else GRIP_OPEN
        if not close:
            clear_visual_hold(self.env)
        self._set_last_gripper_command("close" if close else "open")
        pre_gap = self._gripper_gap()
        last = None
        for _ in range(steps):
            last = self._step(np.zeros(7), grip)
            if last is not None and last.done:
                break
        post_gap = self._gripper_gap()
        if close:
            self._gripper_classifier().confirm_close(pre_gap=pre_gap, post_gap=post_gap)
        else:
            self._gripper_classifier().confirm_open(gap=post_gap)
        return last

    def servo_to(self, pos, quat=None, gripper: str = "keep",
                 pos_tol: float = 0.015, rot_tol: float = 0.10,
                 max_iters: int = 120, via_trajopt: bool = False):
        """Servo the EE to world grip_site pose ``pos`` (and ``quat`` if given).

        Primary path: solve one goal joint config with PyRoKi and drive the
        JOINT_POSITION controller monotonically to it (fast, ~1 ms IK). If the arm
        FREEZES mid-drive (wedged against the table / itself), escape with a
        COLLISION-FREE trajopt path (avoiding self-collision + the table) and
        stream through its waypoints — trajopt is paid ONLY on a wedge, so the
        common move stays fast. Falls back to the per-step Jacobian IK if the
        service is unreachable. ``gripper`` is 'open'|'close'|'keep', held every
        step. Stops early if the episode terminates.
        Returns (reached: bool, last_step).
        """
        try:
            pos = np.asarray(pos, dtype=float)
        except (TypeError, ValueError, OverflowError):
            return False, None
        if pos.shape != (3,) or not np.all(np.isfinite(pos)):
            return False, None
        if quat is not None:
            try:
                quat = np.asarray(quat, dtype=float)
            except (TypeError, ValueError, OverflowError):
                return False, None
            norm = float(np.linalg.norm(quat)) if quat.shape == (4,) else 0.0
            if (
                quat.shape != (4,)
                or not np.all(np.isfinite(quat))
                or norm <= np.finfo(float).eps
            ):
                return False, None
            quat = quat / norm
        grip = {"open": GRIP_OPEN, "close": GRIP_CLOSE}.get(gripper)
        if grip == GRIP_OPEN:
            clear_visual_hold(self.env)
        if getattr(self, "_is_osc", False):
            return self._servo_osc(
                pos,
                quat,
                grip,
                pos_tol,
                rot_tol,
                max_iters,
            )
        q_goal = self._goal_config(pos, quat)
        if q_goal is None:
            return self._servo_jacobian(pos, quat, grip, pos_tol, rot_tol, max_iters)
        if via_trajopt:
            # Proactive collision-free plan for a KNOWN long carry (the place
            # composites request this): plan from the CURRENT good pose up front,
            # not reactively after a jam. Trajopt streaming is bounded to ~135
            # physics steps (10 waypoints × _TRAJOPT_SETTLE) so it fits the 1000-
            # step episode horizon — auto-planning EVERY large move blew the budget
            # (~470 steps/move → horizon exhausted → episode frozen mid-task).
            traj = self._plan_trajopt(pos, quat)
            if traj is not None:
                return self._servo_via_trajopt(traj, grip, pos, quat, pos_tol, rot_tol)
        reached, last, stuck = self._servo_to_config(q_goal, grip, pos, quat, pos_tol, rot_tol)
        if reached or not stuck:
            return reached, last                          # fast common path (no trajopt cost)
        # Wedged mid-drive → escape with a collision-free trajopt path. Trajopt
        # (slow: solve + waypoint following) is paid ONLY on a jam, not every move.
        traj = self._plan_trajopt(pos, quat)
        if traj is not None:
            return self._servo_via_trajopt(traj, grip, pos, quat, pos_tol, rot_tol)
        return reached, last

    def servo_correction_to(
        self,
        pos,
        quat=None,
        gripper: str = "keep",
        pos_tol: float = 0.01,
        rot_tol: float = 0.10,
        max_iters: int = 80,
    ):
        grip = {"open": GRIP_OPEN, "close": GRIP_CLOSE}.get(gripper)
        if grip == GRIP_OPEN:
            clear_visual_hold(self.env)
        if getattr(self, "_is_osc", False):
            return self._servo_osc(
                np.asarray(pos, dtype=float),
                None if quat is None else np.asarray(quat, dtype=float),
                grip,
                pos_tol,
                rot_tol,
                max_iters,
            )
        return self._servo_jacobian(
            pos,
            quat,
            grip,
            pos_tol,
            rot_tol,
            max_iters,
        )

    def _servo_osc(
        self,
        pos: np.ndarray,
        quat: np.ndarray | None,
        grip: float | None,
        pos_tol: float,
        rot_tol: float,
        max_iters: int,
    ):
        target = np.asarray(pos, dtype=float)
        target_quat = None if quat is None else np.asarray(quat, dtype=float)
        last = None
        previous = None
        stalled = 0
        for _ in range(max(1, int(max_iters))):
            current, current_quat, _ = self.read_pose()
            if self._reached(target, target_quat, pos_tol, rot_tol):
                return True, last
            if previous is not None and float(
                np.linalg.norm(np.asarray(current) - previous)
            ) < 1e-5:
                stalled += 1
                if stalled >= _OSC_STALL_STEPS:
                    break
            else:
                stalled = 0
            previous = np.asarray(current, dtype=float).copy()
            command = np.zeros(6, dtype=float)
            command[:3] = np.clip(
                (target - np.asarray(current, dtype=float))
                / _OSC_TRANSLATION_SCALE,
                -1.0,
                1.0,
            )
            if target_quat is not None:
                command[3:] = np.clip(
                    _axisangle_err(
                        np.asarray(current_quat, dtype=float),
                        target_quat,
                    )
                    / _OSC_ROTATION_SCALE,
                    -1.0,
                    1.0,
                )
            active_grip = grip if grip is not None else self._hold_grip()
            last = self._osc_step(command, active_grip)
            if last is not None and last.done:
                break
        return self._reached(target, target_quat, pos_tol, rot_tol), last

    def _plan_trajopt(self, pos, quat) -> np.ndarray | None:
        """Collision-free joint trajectory from the CURRENT EE pose to the target
        ``(pos, quat)``, via PyRoKi over ZMQ. Both endpoints go through the shared
        world↔base transform. Returns ``(timesteps, 7)`` or None if unreachable."""
        cur_pos, cur_quat, _ = self.read_pose()
        start_wxyz, start_base = self._pose_in_base(cur_pos, cur_quat)
        end_wxyz, end_base = self._pose_in_base(pos, quat)
        return _trajopt_zmq(
            start_wxyz,
            start_base,
            end_wxyz,
            end_base,
            self._arm_qpos(),
            _TRAJOPT_TIMESTEPS,
        )

    def _servo_via_trajopt(self, traj, grip, pos, quat, pos_tol, rot_tol):
        """Drive the JOINT_POSITION controller sequentially through each waypoint
        config of a collision-free trajectory (settling briefly at each), then
        snap onto the target with one plain-IK correction. The trajectory's
        collision-aware endpoint IK uses soft pose weights (it trades endpoint
        accuracy for a clear path), so a final tight IK-config drive recovers
        precision now that the arm is out of the jam. Skips the first waypoint
        (it is the current config). Reports cartesian reach against the target."""
        last = None
        for q_way in traj[1:]:
            last = self._drive_to_waypoint(q_way, grip)
            if last is not None and last.done:
                break
        if last is not None and last.done:
            return self._reached(pos, quat, pos_tol, rot_tol), last
        q_goal = self._goal_config(pos, quat)
        if q_goal is not None:
            reached, final_step, _ = self._servo_to_config(
                q_goal,
                grip,
                pos,
                quat,
                pos_tol,
                rot_tol,
            )
            return reached, final_step
        return self._reached(pos, quat, pos_tol, rot_tol), last

    def _drive_to_waypoint(self, q_way, grip):
        """Stream toward one waypoint config: advance the JOINT_POSITION servo
        (saturated) until the arm is LOOSELY near it (``_TRAJOPT_ADVANCE``) or the
        per-waypoint step cap is hit, then move on. Intermediate waypoints need no
        tight settle — the collision-free guarantee is in the path shape, and a
        final IK drive snaps onto the target."""
        last = None
        for _ in range(_TRAJOPT_SETTLE):
            dq = q_way - self._arm_qpos()
            if float(np.max(np.abs(dq))) <= _TRAJOPT_ADVANCE:
                break
            g = grip if grip is not None else self._hold_grip()
            last = self._step(dq, g)
            if last is not None and last.done:
                break
        return last

    def _config_iters(self, q_goal: np.ndarray) -> int:
        """Step budget to traverse from the current arm config to ``q_goal`` at the
        controller's effective rate, with margin, clamped to [min, max]. The move
        stops the instant the goal is reached, so a generous budget is free."""
        dist = float(np.max(np.abs(q_goal - self._arm_qpos())))
        need = int(dist / _CTRL_RATE * _CFG_ITER_MARGIN)
        return int(np.clip(need, _CFG_ITER_MIN, _CFG_ITER_MAX))

    def _servo_to_config(self, q_goal, grip, pos, quat, pos_tol, rot_tol):
        """Drive the arm monotonically to a fixed goal joint config. Returns
        (reached, last, stuck) — ``stuck`` is True if the arm FROZE (joints
        unchanged for several steps while still far from the goal), the signal for
        the caller to fall back to a collision-free trajopt escape."""
        last = None
        prev_q = None
        frozen = 0
        for _ in range(self._config_iters(q_goal)):
            q = self._arm_qpos()
            dq = q_goal - q
            if float(np.max(np.abs(dq))) <= _IK_TOL:
                break
            if prev_q is not None and float(np.max(np.abs(q - prev_q))) < 1e-4:
                frozen += 1
                if frozen >= 6:                          # wedged mid-drive
                    return self._reached(pos, quat, pos_tol, rot_tol), last, True
            else:
                frozen = 0
            prev_q = q
            g = grip if grip is not None else self._hold_grip()
            last = self._step(dq, g)
            if last is not None and last.done:
                break
        return self._reached(pos, quat, pos_tol, rot_tol), last, False

    def _servo_jacobian(self, pos, quat, grip, pos_tol, rot_tol, max_iters):
        """Fallback: per-step damped-least-squares Jacobian IK on the live site
        Jacobian (used only when the PyRoKi service is unreachable)."""
        target = np.asarray(pos, dtype=float)
        q_tgt = None if quat is None else np.asarray(quat, dtype=float)
        last = None
        for _ in range(max_iters):
            cur, cur_q, _ = self.read_pose()
            perr = target - cur
            rerr = _axisangle_err(cur_q, q_tgt) if q_tgt is not None else np.zeros(3)
            if self._reached(pos, quat, pos_tol, rot_tol):
                return True, last
            pn = float(np.linalg.norm(perr))
            if pn > _MAX_ERR:                         # keep the IK direction stable
                perr = perr / pn * _MAX_ERR
            dq = self._ik_dq(perr, rerr, q_tgt is not None)
            g = grip if grip is not None else self._hold_grip()
            last = self._step(dq, g)
            if last is not None and last.done:
                break
        return self._reached(pos, quat, pos_tol, rot_tol), last

    def _reached(self, pos, quat, pos_tol, rot_tol) -> bool:
        """Cartesian reach test against a world grip_site pose target."""
        cur, cur_q, _ = self.read_pose()
        q_tgt = None if quat is None else np.asarray(quat, dtype=float)
        pos_ok = float(np.linalg.norm(np.asarray(pos, dtype=float) - cur)) <= pos_tol
        rot_ok = q_tgt is None or \
            float(np.linalg.norm(_axisangle_err(cur_q, q_tgt))) <= rot_tol
        return pos_ok and rot_ok
