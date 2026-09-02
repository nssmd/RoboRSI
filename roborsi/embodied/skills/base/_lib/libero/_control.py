"""Joint-space inverse-kinematics servo for the base/libero skills.

LIBERO's Franka is driven through robosuite's JOINT_POSITION controller (8-D
action ``[dq1..dq7, gripper]``, gripper ``+1`` close / ``-1`` open). Reaching a
world EE pose is done the way CaP-X does it — SOLVE the full inverse kinematics
for a target joint config, THEN move there in joint space:

  1. ``_solve_ik`` iterates damped least-squares on the MuJoCo site Jacobian using
     a SCRATCH ``MjData`` (the real arm never moves during the solve, so it can't
     wedge mid-iteration), clamping every step to the Panda joint limits and
     biasing the redundant DoF toward a mid-range rest. It returns a VALID joint
     config that hits the target (position, and orientation softly).
  2. ``servo_to`` then commands the JOINT_POSITION controller toward that config —
     a monotone joint interpolation that cannot wedge (the config is reachable).

This replaces the old OSC operational-space P-controller (which froze at kinematic
limits) and the earlier per-step Jacobian servo (whose wrist free-twisted into
wedges on position-only moves). Orientation is weighted 5× below position (CaP's
pyroki solve_ik uses pos_weight=50, ori_weight=10) and defaults to top-down when a
caller gives none, so the wrist is always guided. Pure MuJoCo — no jax/pybullet.
"""

from __future__ import annotations

from typing import Any

import numpy as np

JOINT_STEP = 0.10        # JOINT_POSITION output range at |cmd|=1 (rad); matches adapter's controller.output_max
GRIP_CLOSE = 1.0
GRIP_OPEN = -1.0
_OPEN_GAP = 0.03         # finger gap (qpos[0]-qpos[1]) above this ⇒ open
_DAMP = 0.05             # damped-least-squares λ (stability near singularities)
_MAX_ERR = 0.10          # cap per-iter cartesian error in the solve → stable direction
_ORI_W = 0.2             # orientation weight relative to position (CaP: pos 50 : ori 10)
_NULL_GAIN = 0.0         # rest bias OFF: it leaks through the DAMPED pseudo-inverse and pulls the solve off-target; the joint CLAMP already prevents limit violations
_TOP_DOWN = np.array([1.0, 0.0, 0.0, 0.0])   # default EE orientation (xyzw): +Z → world −Z
_Q_REST = np.array([0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785])   # Panda "ready"
# Panda joint limits (rad) — the solve clamps to these so it never returns a config
# that drives a joint past its stop (the source of the wrist wedges).
_Q_LOWER = np.array([-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973])
_Q_UPPER = np.array([2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973])


def _axisangle_err(q_cur: np.ndarray, q_tgt: np.ndarray) -> np.ndarray:
    """Axis-angle vector (rad, world frame) rotating q_cur → q_tgt. xyzw quats."""
    from robosuite.utils import transform_utils as T
    q_err = T.quat_multiply(q_tgt, T.quat_inverse(q_cur))
    return np.asarray(T.quat2axisangle(q_err), dtype=float)


class LiberoControl:
    """Solve-then-move Jacobian-IK wrapper over a ``LiberoProEnv`` (JOINT_POSITION)."""

    def __init__(self, env: Any) -> None:
        self.env = env
        rs = env._env.env                          # robosuite env under the adapter
        self._model = rs.sim.model._model
        self._data = rs.sim.data._data
        self._site = self._model.site("gripper0_grip_site").id
        self._arm = np.asarray(rs.robots[0]._ref_joint_vel_indexes)   # Jacobian columns
        self._qpos_idx = np.asarray(rs.robots[0]._ref_joint_pos_indexes)   # arm qpos in sim
        self._adim = int(rs.action_dim)            # 8 for JOINT_POSITION (7 + gripper)
        self._scratch = None                       # lazily-created MjData for IK FK

    # ── ground-truth reads ───────────────────────────────────────────────
    def read_pose(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(eef_pos[3], eef_quat[4] xyzw, gripper_qpos[2]) from the last obs."""
        obs = self.env.raw_obs()
        return (np.asarray(obs["robot0_eef_pos"], dtype=float),
                np.asarray(obs["robot0_eef_quat"], dtype=float),
                np.asarray(obs["robot0_gripper_qpos"], dtype=float))

    def is_open(self) -> bool:
        _, _, gq = self.read_pose()
        return float(gq[0] - gq[1]) > _OPEN_GAP

    def _hold_grip(self) -> float:
        """The gripper command that KEEPS the current open/closed state."""
        return GRIP_OPEN if self.is_open() else GRIP_CLOSE

    # ── primitive step ───────────────────────────────────────────────────
    def _step(self, dq: np.ndarray, grip: float):
        action = np.zeros(self._adim, dtype=float)
        action[:7] = np.clip(dq / JOINT_STEP, -1.0, 1.0)
        action[-1] = grip
        return self.env.step(action)     # LiberoProEnv.step → Step; refreshes raw_obs

    def set_gripper(self, close: bool, steps: int = 12):
        """Command open/close and hold (zero joint delta) for ``steps`` ticks so
        the fingers finish moving. Returns the last Step; stops early on done."""
        grip = GRIP_CLOSE if close else GRIP_OPEN
        last = None
        for _ in range(steps):
            last = self._step(np.zeros(7), grip)
            if last is not None and last.done:
                break
        return last

    # ── inverse kinematics (offline solve on a scratch MjData) ────────────
    def _fk(self, q: np.ndarray):
        """(EE pos, EE quat xyzw) at arm config ``q`` on the scratch sim — the real
        arm is not touched."""
        import mujoco
        from robosuite.utils import transform_utils as T
        sd = self._scratch
        sd.qpos[self._qpos_idx] = q
        mujoco.mj_forward(self._model, sd)
        pos = np.array(sd.site_xpos[self._site])
        mat = np.array(sd.site_xmat[self._site]).reshape(3, 3)
        return pos, T.mat2quat(mat)

    def _solve_ik(self, tpos: np.ndarray, tquat: np.ndarray,
                  iters: int = 100, tol: float = 0.004):
        """Damped-least-squares IK on a scratch MjData → a joint config that hits
        ``tpos`` (and ``tquat`` softly), clamped to the Panda joint limits."""
        import mujoco
        if self._scratch is None:
            self._scratch = mujoco.MjData(self._model)
        sd = self._scratch
        sd.qpos[:] = self._data.qpos            # sync the full live state (base, objects)
        q = np.asarray(self._data.qpos)[self._qpos_idx].copy()
        for _ in range(iters):
            pos, quat = self._fk(q)
            perr = tpos - pos
            rerr = _axisangle_err(quat, tquat)
            if float(np.linalg.norm(perr)) < tol and float(np.linalg.norm(rerr)) < 0.05:
                break
            pn = float(np.linalg.norm(perr))
            if pn > _MAX_ERR:
                perr = perr / pn * _MAX_ERR
            jacp = np.zeros((3, self._model.nv)); jacr = np.zeros((3, self._model.nv))
            mujoco.mj_jacSite(self._model, sd, jacp, jacr, self._site)
            J = np.vstack([jacp[:, self._arm], _ORI_W * jacr[:, self._arm]])
            err = np.concatenate([perr, _ORI_W * rerr])
            Jpinv = J.T @ np.linalg.solve(J @ J.T + _DAMP * np.eye(6), np.eye(6))
            dq = Jpinv @ err + (np.eye(7) - Jpinv @ J) @ (_Q_REST - q) * _NULL_GAIN
            q = np.clip(q + dq, _Q_LOWER, _Q_UPPER)
        return q

    # ── composed motion: solve then joint-space move ─────────────────────
    def servo_to(self, pos, quat=None, gripper: str = "keep",
                 pos_tol: float = 0.008, rot_tol: float = 0.10,
                 max_iters: int = 120):
        """Solve IK for ``pos`` (+ ``quat``, else top-down) then drive the arm to
        that joint config. Returns (reached: bool, last_step)."""
        target = np.asarray(pos, dtype=float)
        want_rot = quat is not None
        q_ori = _TOP_DOWN if quat is None else np.asarray(quat, dtype=float)
        grip = {"open": GRIP_OPEN, "close": GRIP_CLOSE}.get(gripper)
        q_goal = self._solve_ik(target, q_ori)
        last = None
        for _ in range(max_iters):
            cur, cur_q, _ = self.read_pose()
            pos_ok = float(np.linalg.norm(target - cur)) <= pos_tol
            rot_ok = (not want_rot) or float(np.linalg.norm(_axisangle_err(cur_q, q_ori))) <= rot_tol
            if pos_ok and rot_ok:
                return True, last
            dqj = q_goal - np.asarray(self._data.qpos)[self._qpos_idx]
            if float(np.linalg.norm(dqj)) < 0.004 and pos_ok:
                return True, last
            g = grip if grip is not None else self._hold_grip()
            last = self._step(dqj, g)
            if last is not None and last.done:
                break
        cur, cur_q, _ = self.read_pose()
        reached = (float(np.linalg.norm(target - cur)) <= pos_tol and
                   (not want_rot or float(np.linalg.norm(_axisangle_err(cur_q, q_ori))) <= rot_tol))
        return reached, last
