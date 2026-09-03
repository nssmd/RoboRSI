"""LIBERO-PRO adapter (robosuite 1.4 + LIBERO, perturbed BDDL task suites).

LIBERO-PRO (`arXiv:2510.03827`) extends the LIBERO benchmark with four
orthogonal perturbation dimensions — task, object, position(swap), language —
baked into per-task BDDL files. It reuses LIBERO's ``OffScreenRenderEnv``
(robosuite 1.4 + MuJoCo) unchanged; only the loaded BDDL / init-state files
differ. This adapter therefore wraps the SAME robosuite family as the RoboCasa
backend, and mirrors its shape.

Checkout layout::

    /path/to/LIBERO-PRO/                  # configured by `roborsi libero configure`
      libero/libero/bddl_files/<suite>/   # base + perturbed suites
      libero/libero/init_files/<suite>/   # matching pruned init states

The import of ``libero`` is deferred into the methods that touch the simulator.
The runtime resolver activates ``ROBORSI_LIBERO_ROOT`` or the persisted
``~/.roborsi/libero.json`` checkout before importing.

API surface we wrap (verified against the installed fork)::

    from libero.libero import benchmark
    from libero.libero.envs import OffScreenRenderEnv
    bi = benchmark.get_benchmark_dict()["libero_goal_task"]()
    bddl = bi.get_task_bddl_file_path(task_id)
    init_states = bi.get_task_init_states(task_id)      # torch.load pickle
    env = OffScreenRenderEnv(bddl_file_name=bddl,
            camera_heights=256, camera_widths=256)
    env.reset(); obs = env.set_init_state(init_states[i])
    obs, reward, done, info = env.step(action)          # action_dim == 7
    instruction = env.language_instruction               # PERTURBED text
    success = env.check_success()                        # -> bool
    env.close()

A "task" here is one ``"<suite>/<task_id>"`` pair, e.g.
``"libero_goal_task/0"`` — one perturbed episode setup. The instruction the
policy must follow is ``env.language_instruction`` (read from the perturbed
BDDL), NOT the benchmark's registry metadata, which still shows the original.

Expert playback: LIBERO ships demonstrations as MimicGen-style HDF5 datasets,
not a scripted ``play_once`` expert, so ``run_expert`` raises
NotImplementedError (as RoboCasa does). ``step`` IS implemented — robosuite
exposes the standard gym-ish ``step(action)`` contract.
"""

from __future__ import annotations

import contextlib
import os
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
# Head + wrist mirror the RoboTwin/RoboCasa convention ("head_camera", "wrist").
_DEFAULT_HEAD_CAMERA = "agentview"
_DEFAULT_WRIST_CAMERA = "robot0_eye_in_hand"
_CAMERA_ALIASES = {
    _DEFAULT_HEAD_CAMERA: "head_camera",
    _DEFAULT_WRIST_CAMERA: "wrist",
}

# Proprioceptive keys we concatenate (in order) into Observation.state.
_PROPRIO_KEYS = ("robot0_joint_pos", "robot0_gripper_qpos")

# The canonical LIBERO-PRO perturbation suites (base set x four dimensions),
# enumerated by ``list_tasks`` by default. Override via ROBORSI_LIBERO_SUITES
# (comma-separated) to include base suites (libero_goal, ...) or the position
# sweeps (libero_object_temp_x0.3, ...).
_BASE_SETS = ("libero_goal", "libero_spatial", "libero_object", "libero_10")
_PERTURB_DIMS = ("task", "object", "swap", "lan")
_DEFAULT_SUITES = tuple(
    f"{base}_{dim}" for base in _BASE_SETS for dim in _PERTURB_DIMS
)


@contextlib.contextmanager
def _torch_full_load():
    """Load pickled init states with ``weights_only=False``.

    LIBERO writes init-state files as numpy pickles and reads them via
    ``torch.load``; torch>=2.6 defaults ``weights_only=True`` and rejects them.
    These are trusted local files, so we temporarily restore full unpickling
    instead of mutating ``torch.load`` process-wide.
    """
    import torch
    original = torch.load

    def _full(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original(*args, **kwargs)

    torch.load = _full
    try:
        yield
    finally:
        torch.load = original


def _extract_images(obs: dict[str, Any]) -> dict[str, Any]:
    """robosuite obs ``<cam>_image`` (HWC uint8 RGB, rendered bottom-up) ->
    {alias: ndarray}, flipped to top-down so downstream views are upright.

    ``rgb[::-1]`` is a NEGATIVE-STRIDE, non-owning VIEW into robosuite's per-step
    render buffer. Consumers that hold the ``Observation`` and read it later — the
    demo-frame ``rollout._on_tick`` capture, the ``look`` tool, and the VLM base64
    encode — then encode a buffer MuJoCo is free to reuse/overwrite under render
    churn (the 3-role runtime), yielding torn-stripe garbage JPEGs. Materialize a
    contiguous, top-down uint8 COPY so ``images`` never aliases the transient
    buffer; the SAM3 grasp path only worked because it copies too
    (``np.asarray(rgb, dtype=uint8)``)."""
    import numpy as np
    images: dict[str, Any] = {}
    for cam, alias in _CAMERA_ALIASES.items():
        rgb = obs.get(f"{cam}_image")
        if rgb is None:
            continue
        images[alias] = np.ascontiguousarray(rgb[::-1], dtype=np.uint8)
    return images


def _extract_state(obs: dict[str, Any]) -> Any:
    """Flat float32 proprio vector: robosuite ``robot0_proprio-state`` when
    present, else joint_pos + gripper_qpos."""
    import numpy as np
    proprio = obs.get("robot0_proprio-state")
    if proprio is not None:
        return np.asarray(proprio, dtype=np.float32).flatten()
    parts = [
        np.asarray(obs[key], dtype=np.float32).flatten()
        for key in _PROPRIO_KEYS
        if obs.get(key) is not None
    ]
    return np.concatenate(parts) if parts else None


def _visible_raw_obs(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only camera/depth and robot-proprioception keys.

    This is a defense-in-depth boundary in case an upstream LIBERO build
    ignores ``use_object_obs=False`` or adds object observations later.
    """
    if not isinstance(raw, dict):
        return {}
    camera_keys = {
        f"{camera}_{suffix}"
        for camera in _CAMERA_ALIASES
        for suffix in ("image", "depth")
    }
    return {
        key: value
        for key, value in raw.items()
        if key.startswith("robot0_") or key in camera_keys
    }


def _to_sim_obs(raw: dict[str, Any] | None, instruction: str) -> Observation:
    visible = _visible_raw_obs(raw)
    if not visible:
        return Observation()
    return Observation(
        images=_extract_images(visible),
        state=_extract_state(visible),
        timestamp=time.time(),
        extras={"instruction": instruction},
    )


class LiberoProEnv(Env):
    """One LIBERO-PRO perturbed task instance (robosuite/MuJoCo)."""

    backend_name = "libero-pro"

    def __init__(
        self,
        env: Any,
        task: str,
        init_states: Any,
        instruction: str,
        settle_steps: int,
        camera_hw: tuple[int, int] = (256, 256),
    ) -> None:
        self._env = env
        self.task = task
        self._init_states = init_states
        self.instruction = instruction        # perturbed :language from the BDDL
        self._settle_steps = settle_steps
        self._camera_hw = camera_hw
        self._last_obs: Observation | None = None
        self._raw: dict[str, Any] = {}     # last robosuite obs dict (ground truth)
        self._terminated: bool = False
        self._tick_cb = None               # rollout frame-capture callback (demo video)
        self._vframes: list = []           # buffered head frames → demo mp4 on success

    def _bind_gl_context(self) -> None:
        """Make MuJoCo's offscreen GL context current on the CALLING thread.

        ROOT CAUSE of the torn-stripe garbage frames: the rollout dispatches every
        tool (grasp/place servos) on a ``ThreadPoolExecutor`` worker thread
        (``rollout._dispatch_with_timeout``). MuJoCo's OSMesa/EGL render context is
        THREAD-AFFINE — it was created on the main thread at ``make_env``/``reset``,
        so ``sim.render`` fired from a worker thread reads an uninitialized/wrong
        buffer and returns frozen RGB-stripe noise (verified: main-thread step
        std~41 = real scene, worker-thread step std~85 = garbage; ``make_current``
        restores std~41). Rebinding the context to whatever thread is about to
        render (cheap, idempotent) fixes it for every consumer — tick frames, the
        ``look`` tool, the VLM image, and the SAM3 grasp/place clouds."""
        rc = getattr(self._env.env.sim, "_render_context_offscreen", None)
        ctx = getattr(rc, "gl_ctx", None) if rc is not None else None
        if ctx is not None:
            ctx.make_current()

    def reset(self, seed: int) -> Observation:
        """Restore a saved (already-settled) init state chosen by ``seed``.

        LIBERO ``pruned_init`` states are valid MuJoCo sim states, so no
        settling is needed by default; ``settle_steps`` no-op steps are taken
        only if configured.
        """
        import numpy as np
        self._bind_gl_context()
        self._env.reset()
        # Widen the JOINT_POSITION step every episode — env.reset() recreates the
        # controller at the default 0.05 rad/step (too slow for the IK servo to
        # reach in the callers' budgets). 0.10 matches _control.JOINT_STEP.
        _jp = self._env.env.robots[0].controller
        _jp.output_max = np.full(7, 0.10)
        _jp.output_min = np.full(7, -0.10)
        idx = int(seed) % len(self._init_states)
        raw = self._env.set_init_state(self._init_states[idx])
        for _ in range(self._settle_steps):
            raw, _, _, _ = self._env.step(_NOOP_ACTION)
        self._raw = _visible_raw_obs(raw)
        self._last_obs = _to_sim_obs(self._raw, self.instruction)
        self._terminated = False
        self._vframes = []
        return self._last_obs

    def step(self, action, action_type: str = "ee") -> "Step":
        """Advance one robosuite step with the 7-D OSC_POSE action
        (dx,dy,dz,droll,dpitch,dyaw,gripper). ``action_type`` is accepted for
        interface parity; LIBERO consumes the controller-space vector directly.

        Task success is deliberately not checked here. The Harness evaluates
        the simulator predicate once, after the Agent tool loop has ended.
        """
        import numpy as np
        if self._terminated or bool(getattr(self._env.env, "done", False)):
            self._terminated = True
            return Step(obs=self._last_obs or Observation(), action=action,
                        reward=0.0, done=True,
                        info={"terminated": True})
        self._bind_gl_context()            # render on the CALLING thread's context
        raw, _reward, done, info = self._env.step(
            np.asarray(action, dtype=np.float64).flatten()
        )
        self._raw = _visible_raw_obs(raw)
        self._last_obs = _to_sim_obs(self._raw, self.instruction)
        info = {
            key: value
            for key, value in dict(info or {}).items()
            if key.lower() not in {"success", "is_success", "task_success"}
        }
        self._terminated = bool(done)
        if self._tick_cb is not None:      # feed the rollout's demo-video frame capture
            self._tick_cb()
        self._capture_frame()              # buffer head frame for the success mp4
        return Step(
            obs=self._last_obs,
            action=action,
            reward=0.0,
            done=bool(done),
            info=info,
        )

    def run_expert(self, seed: int) -> Rollout:
        raise NotImplementedError(
            "LIBERO ships demonstrations as HDF5 datasets, not a scripted "
            "expert; replay a demo file to obtain expert rollouts."
        )

    def _capture_frame(self) -> None:
        """Buffer the current head_camera frame (bounded) for the demo mp4.
        Downscaled to 256px so the buffer stays light regardless of render res."""
        if self._last_obs is None or len(self._vframes) >= 600:
            return
        import numpy as np
        head = self._last_obs.images.get("head_camera")
        if head is None:
            return
        f = np.asarray(head, dtype=np.uint8)
        if f.shape[0] > 256:
            import cv2
            f = cv2.resize(f, (256, 256))
        else:
            f = f.copy()
        self._vframes.append(f)

    def _save_video(self) -> None:
        """Write the buffered episode frames to artifacts/demos/auto/ as an mp4."""
        import time as _t
        from pathlib import Path

        import cv2
        # adapter.py is at roborsi/embodied/sim/libero/ → repo root is parents[4]
        # (libero→sim→embodied→roborsi→repo); demos live under repo/artifacts.
        demos = Path(__file__).resolve().parents[4] / "artifacts" / "demos" / "auto"
        demos.mkdir(parents=True, exist_ok=True)
        safe = str(self.task).replace("/", "_")
        out = demos / f"libero-{safe}-{_t.strftime('%Y%m%d-%H%M%S')}.mp4"
        h, w = self._vframes[0].shape[:2]
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), 20.0, (w, h))
        for f in self._vframes:
            writer.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
        writer.release()

    def close(self) -> None:
        if self._env is None:
            return
        # Keep the mp4 only for episodes the sim judged successful — the demo
        # library should hold WINS, not every attempt. check_success() needs the
        # inner env alive, so query BEFORE closing it.
        if self._vframes and bool(self._env.check_success()):
            self._save_video()
        self._env.close()
        self._env = None

    # ── Seam methods for the universal rollout loop ──────────────────────

    def take_snapshot(self) -> Observation:
        """Latest cached obs (robosuite has no cheap re-poll without a step)."""
        return self._last_obs or Observation()

    def check_success(self) -> bool | None:
        """LIBERO's ground-truth predicate over the (perturbed) goal state."""
        return bool(self._env.check_success())

    # ── Data accessors for base/libero skills (encapsulate the robosuite env) ──

    def raw_obs(self) -> dict[str, Any]:
        """Last robot-proprioception and camera/depth observation.

        The adapter filters LIBERO's internal observation before storing it, so
        this mapping does not contain simulator object poses.
        """
        return self._raw

    def robot_base_pos(self):
        """World XYZ of the Franka base. The arm has no motion planner, so a
        reachability skill can only estimate 'can it get there' from the base
        position + a reach radius — this exposes that origin."""
        import numpy as np
        return np.asarray(
            self._env.env.sim.data.get_body_xpos("robot0_base"), dtype=float)

    def camera_matrices(self, camera: str = "agentview"):
        """(K[3x3] intrinsic, E[4x4] world←camera extrinsic) for a named camera,
        from robosuite ``camera_utils`` — lets a pixel be unprojected to world XYZ."""
        import numpy as np
        from robosuite.utils import camera_utils as cu
        sim = self._env.env.sim
        h, w = self._camera_hw
        k = np.asarray(cu.get_camera_intrinsic_matrix(sim, camera, h, w), dtype=float)
        e = np.asarray(cu.get_camera_extrinsic_matrix(sim, camera), dtype=float)
        return k, e

    def depth_map(self, camera: str = "agentview"):
        """Metric depth (H×W float32, meters) for a named camera, or None if the
        env was built without ``camera_depths``. robosuite returns normalized
        depth in obs; convert to metric via ``camera_utils.get_real_depth_map``."""
        import numpy as np
        raw = self._raw.get(f"{camera}_depth")
        if raw is None:
            return None
        from robosuite.utils import camera_utils as cu
        # MuJoCo's depth buffer is normalized to [0, 1] by definition, but a
        # pixel on the far clip plane can float-overshoot to 1+eps, and a pixel
        # the camera can't resolve (the wrist cam buried against a grasped
        # object) can come back NaN — either trips get_real_depth_map's strict
        # `0 <= d <= 1` assertion and crashes the whole episode on one bad pixel.
        # nan_to_num maps NaN→far(1.0)/±inf→[0,1] first, then clip to the domain.
        raw = np.nan_to_num(np.asarray(raw, dtype=np.float32),
                            nan=1.0, posinf=1.0, neginf=0.0)
        raw = np.clip(raw, 0.0, 1.0)
        metric = np.asarray(cu.get_real_depth_map(self._env.env.sim, raw), dtype=float)
        # robosuite's obs depth buffer is stored bottom-up (row 0 = bottom), like
        # the raw RGB. ``_extract_images`` flips the RGB to top-down and the camera
        # projection (``get_camera_transform_matrix``) is top-down too, so the depth
        # MUST be flipped to match — otherwise pixel_to_world / point clouds land
        # ~0.3 m off (the old "unproject returns garbage" bug that broke every
        # vision-localization consumer).
        return metric[::-1]

    def pixel_to_world(self, u: int, v: int, camera: str = "agentview"):
        """Back-project image pixel ``(u=col, v=row)`` to a world XYZ using the
        camera's depth + transform (robosuite ``transform_from_pixels_to_world``,
        which takes pixels as [row, col]). Returns None if depth is unavailable."""
        import numpy as np
        from robosuite.utils import camera_utils as cu
        depth = self.depth_map(camera)
        if depth is None:
            return None
        h, w = self._camera_hw
        cam2world = np.linalg.inv(cu.get_camera_transform_matrix(
            self._env.env.sim, camera, h, w))
        pt = cu.transform_from_pixels_to_world(
            np.array([int(v), int(u)]), depth, cam2world)
        return np.asarray(pt, dtype=float)

    def hook_physics_step(self, callback):
        """Register a per-sim-step frame-capture callback for the rollout.

        LIBERO has no separate SAPIEN physics loop, but EVERY servo iteration
        calls ``step()``, so we fire ``callback`` there — giving smooth per-frame
        capture that ``_finalize_demo_video`` stitches into a demo mp4 on sim
        success. (Previously LIBERO produced NO video: the base hook was a no-op,
        so no ``tick_*.jpg`` frames were ever written.)"""
        self._tick_cb = callback
        return lambda: setattr(self, "_tick_cb", None)

    # tool_handlers inherits the Env default ({}): LIBERO has no native _do_*
    # tools; the loop resolves tools only via plugin/base-skill dispatch.


_NOOP_ACTION = [0.0] * 7 + [-1.0]        # JOINT_POSITION: hold joints, gripper open


class LiberoProBackend(Backend):
    """Registry entry for LIBERO-PRO (perturbed LIBERO suites on robosuite 1.4)."""

    name = "libero-pro"

    def __init__(self, suites: tuple[str, ...] = _DEFAULT_SUITES) -> None:
        env_suites = os.environ.get("ROBORSI_LIBERO_SUITES")
        self._suites = tuple(env_suites.split(",")) if env_suites else suites

    def available(self) -> tuple[bool, str]:
        try:
            from roborsi.embodied.sim.libero.runtime import activate_runtime

            activate_runtime()
            import libero.libero  # noqa: F401
            import mujoco  # noqa: F401
            import robosuite  # noqa: F401
        except Exception as exc:
            return False, (
                f"LIBERO runtime unavailable: {type(exc).__name__}: {exc}. "
                "Run `roborsi libero configure --root /path/to/LIBERO-PRO` "
                "and `roborsi libero doctor`."
            )
        return True, ""

    def _benchmark_dict(self) -> dict[str, Any]:
        try:
            from roborsi.embodied.sim.libero.runtime import activate_runtime

            activate_runtime()
            from libero.libero import benchmark
        except Exception as exc:
            raise BackendUnavailable(
                f"LIBERO runtime unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        return benchmark.get_benchmark_dict()

    def list_tasks(self) -> list[str]:
        """Enumerate ``"<suite>/<task_id>"`` over the configured suites."""
        bdict = self._benchmark_dict()
        tasks: list[str] = []
        for suite in self._suites:
            if suite not in bdict:
                continue
            for i in range(bdict[suite]().n_tasks):
                tasks.append(f"{suite}/{i}")
        return tasks

    def make_env(self, task: str, config: dict[str, Any] | None = None) -> Env:
        os.environ.setdefault("MUJOCO_GL", "egl")
        cfg = config or {}
        suite, task_id = self._parse_task(task)
        bdict = self._benchmark_dict()
        if suite not in bdict:
            raise BackendUnavailable(
                f"unknown LIBERO suite '{suite}'. known: {sorted(bdict)}"
            )

        from libero.libero.envs import OffScreenRenderEnv
        bench = bdict[suite]()
        bddl = bench.get_task_bddl_file_path(task_id)
        with _torch_full_load():
            init_states = self._load_init_states(bench, task_id)

        # Env physics/depth/VLM render at 256 — the stable, fast baseline. Native
        # 512 destabilises the 3-role's offscreen MjrContext creation ("framebuffer
        # not complete") under render churn. The ONLY measured 512 gain is OWLv2
        # discrimination (3/7→4/7), captured by an on-demand 512 detection render
        # in locate_by_owlv2 (768/1024 regress — the rest is a SAM3 job).
        _res = int(os.environ.get("ROBORSI_LIBERO_RES", "256"))
        cam_h = cfg.get("camera_heights", _res)
        cam_w = cfg.get("camera_widths", _res)
        env = OffScreenRenderEnv(
            bddl_file_name=bddl,
            controller="JOINT_POSITION",   # Jacobian-IK servo (LiberoControl); OSC wedged at joint limits
            ignore_done=True,
            # LIBERO-PRO 0.1 assumes object observables exist while building its
            # sensor list. Keep them inside the simulator, then remove every
            # object key at the adapter boundary via _visible_raw_obs().
            use_object_obs=True,
            camera_heights=cam_h,
            camera_widths=cam_w,
            camera_depths=cfg.get("camera_depths", True),  # enables unproject/pixel skills
        )
        env.reset()
        # Speed up the JOINT_POSITION controller: the default output range is
        # 0.05 rad/step, too slow for the Jacobian-IK servo to reach in the
        # callers' iteration budgets (it stalled a few cm short). 0.10 rad/step
        # (kept in sync with _control.JOINT_STEP) reaches like CaP's blocking move.
        import numpy as _np
        _jp = env.env.robots[0].controller
        _jp.output_max = _np.full(7, 0.10)
        _jp.output_min = _np.full(7, -0.10)
        return LiberoProEnv(
            env=env,
            task=task,
            init_states=init_states,
            instruction=env.language_instruction,
            # LIBERO pruned_init states start objects slightly floating; step a
            # few no-ops so they settle to stable poses before skills read them
            # (otherwise a grasp targets the un-settled height and misses).
            settle_steps=cfg.get("settle_steps", 20),
            camera_hw=(cam_h, cam_w),
        )

    @staticmethod
    def _load_init_states(bench, task_id):
        """Init-states for the task. If ROBORSI_LIBERO_INITDIR is set, load the
        regenerated .pruned_init from <INITDIR>/<problem_folder>/<file> (the
        ASPIRE-protocol run uses 70 states per task so seeds 1-50 / 51-65 are
        disjoint); otherwise use the shipped 50-state files. Non-destructive."""
        from roborsi.embodied.sim.libero.runtime import configured_initdir

        configured = configured_initdir()
        root = os.environ.get("ROBORSI_LIBERO_INITDIR") or (
            str(configured) if configured else None
        )
        if not root:
            return bench.get_task_init_states(task_id)
        task = bench.tasks[task_id]
        path = os.path.join(root, task.problem_folder, task.init_states_file)
        if not os.path.exists(path):
            # only the ASPIRE suites are regenerated; anything else (e.g. the
            # atomic's default libero_object/0) uses the shipped 50-state files.
            return bench.get_task_init_states(task_id)
        import torch
        return torch.load(path)

    @staticmethod
    def _parse_task(task: str) -> tuple[str, int]:
        suite, _, tail = task.rpartition("/")
        if not suite:
            return task, 0                    # bare suite -> first task
        return suite, int(tail)
