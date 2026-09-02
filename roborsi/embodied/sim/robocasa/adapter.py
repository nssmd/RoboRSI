"""RoboCasa adapter (robosuite 1.5.2 + robocasa, 396 kitchen tasks).

RoboCasa ships inside its own conda env (``robocasa``) on top of a patched
robosuite 1.5.2. We do *not* pip-install it into the agent venv — the import
of ``robocasa`` / ``robosuite`` is deferred into the methods that actually
touch the simulator, so this module imports cleanly on any box (the registry
loader in ``sim/__init__.py`` can introspect the backend without SAPIEN/mujoco
present). ``available()`` is the only place that probes the import.

API surface we wrap (verified against the installed env)::

    import robocasa, robosuite                 # importing robocasa registers
                                               # all 396 kitchen tasks
    from robosuite.controllers import load_composite_controller_config
    cfg = load_composite_controller_config(robot="PandaOmron")
    env = robosuite.make(task, robots="PandaOmron", controller_configs=cfg,
            has_renderer=False, has_offscreen_renderer=True,
            use_camera_obs=True,
            camera_names=["robot0_agentview_center", "robot0_eye_in_hand"],
            control_freq=20)
    obs = env.reset()                          # dict; camera images land under
                                               # "<cam>_image" keys (HWC uint8)
    obs, reward, done, info = env.step(action) # action_dim == 12
    success = env._check_success()             # -> bool
    env.close()

Camera naming: robocasa kitchen scenes have *no* plain ``agentview`` camera.
The valid cameras are ``robot0_agentview_center/left/right``, ``robot0_frontview``,
``robot0_robotview`` and ``robot0_eye_in_hand``. We therefore default the head
view to ``robot0_agentview_center`` and the wrist view to ``robot0_eye_in_hand``,
and surface both under stable Observation keys (``head_camera`` / ``wrist``)
so downstream code matches the RoboTwin convention.

Expert playback: robocasa tasks have no generic scripted ``play_once`` expert
(demonstrations come from MimicGen dataset replay), so ``run_expert`` raises
NotImplementedError. ``step`` *is* implemented because robosuite exposes the
standard gym-ish ``step(action)`` contract.
"""

from __future__ import annotations

import time
from typing import Any

from roborsi.embodied.agent_loop.env import (
    Backend,
    BackendUnavailable,
    Env,
    Observation,
    Rollout,
    Step,
)


# Camera mapping: robosuite obs key (``<cam>_image``) -> Observation.images key.
# Head + wrist mirror the RoboTwin/codeact convention ("head_camera", "wrist").
_DEFAULT_HEAD_CAMERA = "robot0_agentview_center"
_DEFAULT_WRIST_CAMERA = "robot0_eye_in_hand"
_CAMERA_ALIASES = {
    _DEFAULT_HEAD_CAMERA: "head_camera",
    _DEFAULT_WRIST_CAMERA: "wrist",
    "agentview": "head_camera",          # alias if a caller asks for it anyway
}

# Proprioceptive keys we concatenate (in order) into Observation.state.
_PROPRIO_KEYS = ("robot0_joint_pos", "robot0_gripper_qpos")


def _extract_images(obs: dict[str, Any], camera_names: list[str]) -> dict[str, Any]:
    """robosuite obs ``<cam>_image`` (HWC uint8 RGB) -> {alias_or_cam: ndarray}."""
    images: dict[str, Any] = {}
    for cam in camera_names:
        rgb = obs.get(f"{cam}_image")
        if rgb is None:
            continue
        key = _CAMERA_ALIASES.get(cam, cam)
        images[key] = rgb
    return images


def _extract_state(obs: dict[str, Any]) -> Any:
    """Flat float32 proprio vector.

    Prefer robosuite's pre-concatenated ``robot0_proprio-state`` when present;
    otherwise assemble joint_pos + gripper_qpos.
    """
    import numpy as np
    proprio = obs.get("robot0_proprio-state")
    if proprio is not None:
        return np.asarray(proprio, dtype=np.float32).flatten()
    parts = []
    for key in _PROPRIO_KEYS:
        v = obs.get(key)
        if v is None:
            continue
        parts.append(np.asarray(v, dtype=np.float32).flatten())
    if parts:
        return np.concatenate(parts)
    return None


def _to_sim_obs(raw: dict[str, Any] | None, camera_names: list[str]) -> Observation:
    if not isinstance(raw, dict):
        return Observation()
    image_keys = {f"{c}_image" for c in camera_names}
    extras = {
        k: v for k, v in raw.items()
        if k not in image_keys and k != "robot0_proprio-state"
    }
    return Observation(
        images=_extract_images(raw, camera_names),
        state=_extract_state(raw),
        timestamp=time.time(),
        extras=extras,
    )


class RoboCasaEnv(Env):
    """One robosuite/robocasa kitchen task instance."""

    backend_name = "robocasa"

    def __init__(self, env: Any, task: str, camera_names: list[str]) -> None:
        self._env = env
        self.task = task
        self._camera_names = camera_names
        self._last_obs: Observation | None = None

    def reset(self, seed: int) -> Observation:
        import numpy as np
        np.random.seed(seed)
        setter = getattr(self._env, "seed", None)
        if callable(setter):
            setter(seed)
        raw = self._env.reset()
        self._last_obs = _to_sim_obs(raw, self._camera_names)
        return self._last_obs

    def step(self, action, action_type: str = "qpos") -> Step:
        """Advance one robosuite step.

        ``action`` is the 12-D robocasa control vector (mobile base + arm +
        gripper). ``action_type`` is accepted for interface parity but robosuite
        consumes the raw controller-space action directly.
        """
        import numpy as np
        raw, reward, done, info = self._env.step(
            np.asarray(action, dtype=np.float64).flatten()
        )
        sim_obs = _to_sim_obs(raw, self._camera_names)
        self._last_obs = sim_obs
        success = bool(self._env._check_success())
        info = dict(info or {})
        info["success"] = success
        return Step(
            obs=sim_obs,
            action=action,
            reward=float(reward),
            done=bool(done) or success,
            info=info,
        )

    def run_expert(self, seed: int) -> Rollout:
        raise NotImplementedError(
            "robocasa has no generic scripted expert; use MimicGen dataset "
            "replay (collect demos / replay HDF5) to obtain expert rollouts."
        )

    def close(self) -> None:
        if self._env is None:
            return
        self._env.close()
        self._env = None

    # ── Seam methods for the universal rollout loop ──────────────────────

    def take_snapshot(self) -> Observation:
        """Latest cached obs (robosuite has no cheap re-poll without a step)."""
        return self._last_obs or Observation()

    def check_success(self) -> bool | None:
        """robosuite's ground-truth predicate."""
        return bool(self._env._check_success())

    # hook_physics_step / tool_handlers inherit the Env defaults (no-op / {}):
    # robocasa has no SAPIEN-style steppable physics loop and no native _do_*
    # tools yet, so the universal loop captures nothing mid-tool and resolves
    # tools only via plugin/base-skill dispatch.


class RoboCasaBackend(Backend):
    """Registry entry for RoboCasa (396 kitchen tasks on robosuite 1.5.2)."""

    name = "robocasa"

    def available(self) -> tuple[bool, str]:
        try:
            import robocasa  # noqa: F401  (import registers the 396 tasks)
            import robosuite  # noqa: F401
        except ImportError as exc:
            return False, (
                f"robocasa/robosuite not importable: {exc}. Activate the "
                "'robocasa' conda env (robosuite 1.5.2 + robocasa)."
            )
        return True, ""

    def list_tasks(self) -> list[str]:
        try:
            import robocasa  # noqa: F401  (registers tasks as a side effect)
            import robosuite
        except ImportError as exc:
            raise BackendUnavailable(
                f"robocasa/robosuite not importable: {exc}. Activate the "
                "'robocasa' conda env."
            ) from exc
        return sorted(robosuite.ALL_ENVIRONMENTS)

    def make_env(self, task: str, config: dict[str, Any] | None = None) -> Env:
        try:
            import robocasa  # noqa: F401  (registers the 396 kitchen tasks)
            import robosuite
            from robosuite.controllers import load_composite_controller_config
        except ImportError as exc:
            raise BackendUnavailable(
                f"robocasa/robosuite not importable: {exc}. Activate the "
                "'robocasa' conda env."
            ) from exc

        cfg = config or {}
        robot = cfg.get("robot", "PandaOmron")
        camera_names = list(cfg.get(
            "camera_names", [_DEFAULT_HEAD_CAMERA, _DEFAULT_WRIST_CAMERA]
        ))
        controller_configs = load_composite_controller_config(robot=robot)
        env = robosuite.make(
            task,
            robots=robot,
            controller_configs=controller_configs,
            has_renderer=False,
            has_offscreen_renderer=cfg.get("require_depth", True),
            use_camera_obs=True,
            camera_names=camera_names,
            control_freq=cfg.get("control_freq", 20),
        )
        return RoboCasaEnv(env=env, task=task, camera_names=camera_names)
