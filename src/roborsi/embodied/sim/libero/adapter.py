"""LIBERO short adapter over robosuite and MuJoCo.

The public catalog contains the 10 spatial, 10 object, 10 goal, and 90 task
suites. Imports stay lazy so configuration and evidence replay work without a
simulator installation. The runtime instruction comes from the current BDDL
episode. Final success adjudication remains a host-only post-episode operation;
it is not registered as an agent tool or included in role prompts.
"""

from __future__ import annotations

import contextlib
import math
import os
import time
from pathlib import Path
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
# Head + wrist mirror the LIBERO convention ("head_camera", "wrist").
_DEFAULT_HEAD_CAMERA = "agentview"
_DEFAULT_WRIST_CAMERA = "robot0_eye_in_hand"
_CAMERA_ALIASES = {
    _DEFAULT_HEAD_CAMERA: "head_camera",
    _DEFAULT_WRIST_CAMERA: "wrist",
}

_ORBIT_VIEW_SPECS = (
    ("orbit_front", 0.0, -30.0),
    ("orbit_left", 90.0, -30.0),
    ("orbit_back", 180.0, -30.0),
    ("orbit_right", 270.0, -30.0),
    ("orbit_top", 35.0, -75.0),
)

# Proprioceptive keys we concatenate (in order) into Observation.state.
_PROPRIO_KEYS = ("robot0_joint_pos", "robot0_gripper_qpos")

# The canonical LIBERO-PRO perturbation suites (base set x four dimensions),
# enumerated by ``list_tasks`` by default. Override via ROBORSI_LIBERO_SUITES
# (comma-separated) to include base suites (libero_goal, ...) or the position
# sweeps (libero_object_temp_x0.3, ...).
_DEFAULT_SUITES = ("libero_spatial", "libero_object", "libero_goal", "libero_90")
_PREVIEW_MEDIA_DIR = {
    "task_success": "success",
    "task_failure": "failure",
    "provider_failure": "infrastructure",
    "transport_failure": "infrastructure",
    "image_failure": "infrastructure",
    "resource_failure": "infrastructure",
    "interrupted": "infrastructure",
    "implementation_failure": "failure",
}


def resolve_init_state_index(*, seed: int, state_count: int) -> int:
    count = int(state_count)
    value = int(seed)
    if count <= 0:
        raise ValueError("init-state bank must be non-empty")
    if value < 0 or value > count:
        raise ValueError(
            f"seed {value} is outside init-state bank of size {count}"
        )
    return value % count


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


def _to_sim_obs(raw: dict[str, Any] | None, instruction: str) -> Observation:
    if not isinstance(raw, dict):
        return Observation()
    return Observation(
        images=_extract_images(raw),
        state=_extract_state(raw),
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
        self._vframes: list[Any] = []      # adapter diagnostic preview frames (RGB)
        self._vframe_steps: list[int | None] = []
        self._preview_max_frames = self._preview_frame_budget()
        self._preview_stride = 1
        self._preview_next_capture_timestep = 1
        self._preview_last_frame = None
        self._preview_last_timestep: int | None = None
        self._orbit_frames: dict[str, Any] = {}
        self._orbit_generation = 0

    @staticmethod
    def _preview_frame_budget() -> int:
        raw = os.environ.get("ROBORSI_LIBERO_PREVIEW_MAX_FRAMES", "256")
        try:
            return max(1, int(raw))
        except ValueError:
            return 256

    def _reset_preview_sampler(self) -> None:
        self._vframes = []
        self._vframe_steps = []
        self._preview_last_frame = None
        self._preview_last_timestep = None
        self._preview_max_frames = self._preview_frame_budget()
        _, horizon = self._episode_progress()
        if horizon is not None and horizon > 0:
            self._preview_stride = max(1, int(math.ceil(horizon / self._preview_max_frames)))
        else:
            self._preview_stride = 1
        self._preview_next_capture_timestep = 1

    def _episode_progress(self) -> tuple[int | None, int | None]:
        inner = getattr(self._env, "env", None)
        if inner is None:
            return None, None
        timestep = getattr(inner, "timestep", None)
        horizon = getattr(inner, "horizon", None)
        if timestep is None:
            timestep = getattr(inner, "_elapsed_steps", None)
        if horizon is None:
            horizon = getattr(inner, "_max_episode_steps", None)
        if timestep is None or horizon is None:
            return None, None
        return int(timestep), int(horizon)

    def _horizon_done(self) -> bool:
        timestep, horizon = self._episode_progress()
        if timestep is None or horizon is None:
            return False
        return timestep >= horizon

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
        idx = resolve_init_state_index(
            seed=seed,
            state_count=len(self._init_states),
        )
        raw = self._env.set_init_state(self._init_states[idx])
        settle_action = np.zeros(int(self._env.env.action_dim), dtype=float)
        settle_action[-1] = -1.0
        for _ in range(self._settle_steps):
            raw, _, _, _ = self._env.step(settle_action)
        # Speed up the JOINT_POSITION controller: the default kp=50 / output_max=0.05
        # advances only ~0.008 rad/step, so a full-arm IK-config move needs ~180
        # steps (~155 s per grasp). Stiffen it (bigger commanded delta + higher gain,
        # critically damped) so moves reach in tens of steps. env.reset() recreates
        # the controller, so set it EVERY episode. Kept in sync with _control.JOINT_STEP.
        _jp = self._env.env.robots[0].controller
        if int(self._env.env.action_dim) == 8:
            _jp.output_max = np.full(7, 0.35)
            _jp.output_min = np.full(7, -0.35)
            _jp.kp = np.full(7, 300.0)
            _jp.kd = np.full(7, 2.0 * np.sqrt(300.0))
        self._raw = raw if isinstance(raw, dict) else {}
        self._last_obs = _to_sim_obs(raw, self.instruction)
        self._terminated = False
        self._orbit_frames = {}
        self._orbit_generation = int(getattr(self, "_orbit_generation", 0)) + 1
        self._reset_preview_sampler()
        return self._last_obs

    def _capture_preview_frame(self, *, timestep: int | None) -> None:
        if self._last_obs is None:
            return
        head = self._last_obs.images.get("head_camera")
        if head is None:
            return
        import numpy as np
        frame = np.ascontiguousarray(head, dtype=np.uint8)
        self._preview_last_frame = frame
        self._preview_last_timestep = int(timestep) if timestep is not None else None
        if len(self._vframes) >= self._preview_max_frames:
            return
        should_capture = False
        if timestep is None:
            should_capture = True
        elif not self._vframes:
            should_capture = True
        elif int(timestep) >= self._preview_next_capture_timestep:
            should_capture = True
        if not should_capture:
            return
        self._vframes.append(frame)
        self._vframe_steps.append(int(timestep) if timestep is not None else None)
        if timestep is not None:
            while self._preview_next_capture_timestep <= int(timestep):
                self._preview_next_capture_timestep += self._preview_stride

    def step(self, action, action_type: str = "ee") -> Step:
        """Advance one robosuite step with the 7-D OSC_POSE action
        (dx,dy,dz,droll,dpitch,dyaw,gripper). ``action_type`` is accepted for
        interface parity; LIBERO consumes the controller-space vector directly.

        Once the underlying episode has terminated (task success or horizon),
        robosuite refuses further steps — servo loops that overshoot would then
        crash. We short-circuit to a no-op terminal Step so they unwind cleanly.
        ``Step.info`` must not expose any underlying done/success bits.
        """
        import numpy as np
        self._orbit_frames = {}
        self._orbit_generation = int(getattr(self, "_orbit_generation", 0)) + 1
        if self._terminated or self._horizon_done():
            self._terminated = True
            timestep, horizon = self._episode_progress()
            return Step(
                obs=self._last_obs or Observation(),
                action=action,
                reward=0.0,
                done=True,
                info={
                    "terminated": True,
                    "reason": "horizon_reached",
                    "timestep": timestep,
                    "horizon": horizon,
                },
            )
        self._bind_gl_context()            # render on the CALLING thread's context
        raw, reward, done, info = self._env.step(
            np.asarray(action, dtype=np.float64).flatten()
        )
        self._raw = raw if isinstance(raw, dict) else {}
        self._last_obs = _to_sim_obs(raw, self.instruction)
        timestep, horizon = self._episode_progress()
        self._capture_preview_frame(timestep=timestep)
        # Ignore robosuite's done bit because LIBERO's base domain sets it from
        # success predicate; termination here must be horizon-only.
        self._terminated = self._horizon_done()
        out_info = {
            "timestep": timestep,
            "horizon": horizon,
        }
        if self._tick_cb is not None:      # feed the rollout's demo-video frame capture
            self._tick_cb(np.asarray(action, dtype=np.float64).copy())
        return Step(
            obs=self._last_obs,
            action=action,
            reward=0.0,
            done=self._terminated,
            info=out_info,
        )

    def run_expert(self, seed: int) -> Rollout:
        raise NotImplementedError(
            "LIBERO ships demonstrations as HDF5 datasets, not a scripted "
            "expert; replay a demo file to obtain expert rollouts."
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
        """LIBERO's ground-truth predicate over the (perturbed) goal state."""
        return bool(self._env.check_success())

    # ── Data accessors for base/libero skills (encapsulate the robosuite env) ──

    def raw_obs(self) -> dict[str, Any]:
        """The last robosuite obs dict — ground-truth EE/object poses,
        joint state and depth/RGB. Base skills read poses from here rather than
        reaching through ``env._env``. Populated on every reset/step."""
        return self._raw

    def parsed_problem(self) -> dict[str, Any]:
        """The BDDL-parsed problem: objects, obj_of_interest, goal_state,
        regions, language_instruction. Used by describe_scene to enumerate the
        scene's real object names."""
        return self._env.env.parsed_problem

    def region_box(self, region_name: str):
        """World-frame acceptance box ``(center[3], half[3])`` of a named site
        region (e.g. ``basket_1_contain_region``), or None if absent. Mirrors
        exactly LIBERO's ``SiteObject.in_box`` extent (``|site_mat @ size|``) so a
        placer can aim for the region the success predicate actually tests."""
        import numpy as np
        inner = self._env.env
        sim = inner.sim
        if region_name not in sim.model.site_names:
            return None
        pos = np.asarray(sim.data.get_site_xpos(region_name), dtype=float)
        mat = np.asarray(sim.data.get_site_xmat(region_name), dtype=float).reshape(3, 3)
        half = np.abs(mat @ np.asarray(inner.get_object(region_name).size, dtype=float))
        return pos, half

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

    def _vision_workspace_center(self):
        """Estimate a free-camera look-at point from current agentview RGB-D."""
        import numpy as np

        depth = self.depth_map("agentview")
        if depth is None:
            return np.asarray([0.0, 0.0, 0.8], dtype=np.float64)
        depth = np.asarray(depth, dtype=np.float64)
        if depth.ndim == 3 and depth.shape[-1] == 1:
            depth = depth[..., 0]
        if depth.ndim != 2:
            return np.asarray([0.0, 0.0, 0.8], dtype=np.float64)
        intrinsic, camera_to_world = self.camera_matrices("agentview")
        height, width = depth.shape
        stride = max(1, min(height, width) // 32)
        rows, columns = np.mgrid[0:height:stride, 0:width:stride]
        z = depth[rows, columns].reshape(-1)
        columns = columns.reshape(-1).astype(np.float64)
        rows = rows.reshape(-1).astype(np.float64)
        valid = np.isfinite(z) & (z > 0.05) & (z < 3.0)
        if int(valid.sum()) < 16:
            return np.asarray([0.0, 0.0, 0.8], dtype=np.float64)
        z = z[valid]
        x = (columns[valid] - intrinsic[0, 2]) * z / intrinsic[0, 0]
        y = (rows[valid] - intrinsic[1, 2]) * z / intrinsic[1, 1]
        camera_points = np.column_stack([x, y, z, np.ones_like(z)])
        world = (camera_to_world @ camera_points.T).T[:, :3]
        finite = world[np.all(np.isfinite(world), axis=1)]
        if len(finite) < 16:
            return np.asarray([0.0, 0.0, 0.8], dtype=np.float64)
        return np.median(finite, axis=0)

    def capture_orbit_views(self, *, image_size: int = 512):
        """Render calibrated free-camera RGB-D views without stepping physics."""
        import numpy as np

        from roborsi.embodied.sim.libero.orbit_geometry import OrbitFrame

        size = max(64, min(512, int(image_size)))
        self._bind_gl_context()
        context = self._env.env.sim._render_context_offscreen
        camera = context.cam
        saved = {
            "type": int(camera.type),
            "fixedcamid": int(camera.fixedcamid),
            "lookat": np.asarray(camera.lookat, dtype=np.float64).copy(),
            "distance": float(camera.distance),
            "azimuth": float(camera.azimuth),
            "elevation": float(camera.elevation),
        }
        frames: dict[str, OrbitFrame] = {}
        try:
            camera.type = 0
            camera.fixedcamid = -1
            camera.lookat[:] = self._vision_workspace_center()
            camera.distance = 1.3
            for name, azimuth, elevation in _ORBIT_VIEW_SPECS:
                camera.azimuth = float(azimuth)
                camera.elevation = float(elevation)
                context.render(size, size, camera_id=-1)
                rgb_raw, depth_raw = context.read_pixels(size, size, depth=True)
                rgb = np.ascontiguousarray(np.flipud(np.asarray(rgb_raw))[..., :3], dtype=np.uint8)
                depth_buffer = np.flipud(np.asarray(depth_raw, dtype=np.float64))
                left, right = context.scn.camera[0], context.scn.camera[1]
                near = float(left.frustum_near)
                far = float(left.frustum_far)
                depth_m = near / (1.0 - depth_buffer * (1.0 - near / far))
                position = (
                    np.asarray(left.pos, dtype=np.float64)
                    + np.asarray(right.pos, dtype=np.float64)
                ) / 2.0
                forward = (
                    np.asarray(left.forward, dtype=np.float64)
                    + np.asarray(right.forward, dtype=np.float64)
                ) / 2.0
                forward /= max(float(np.linalg.norm(forward)), 1e-12)
                up = (
                    np.asarray(left.up, dtype=np.float64)
                    + np.asarray(right.up, dtype=np.float64)
                ) / 2.0
                up /= max(float(np.linalg.norm(up)), 1e-12)
                right_axis = np.cross(forward, up)
                right_axis /= max(float(np.linalg.norm(right_axis)), 1e-12)
                rotation = np.column_stack([right_axis, -up, forward])
                focal = (size / 2.0) * near / max(
                    float(left.frustum_top), 1e-12
                )
                frames[name] = OrbitFrame(
                    name=name,
                    rgb=rgb,
                    depth_m=depth_m,
                    camera_position_world=position,
                    camera_to_world_rotation=rotation,
                    fx=focal,
                    fy=focal,
                    cx=size / 2.0,
                    cy=size / 2.0,
                )
        finally:
            camera.type = saved["type"]
            camera.fixedcamid = saved["fixedcamid"]
            camera.lookat[:] = saved["lookat"]
            camera.distance = saved["distance"]
            camera.azimuth = saved["azimuth"]
            camera.elevation = saved["elevation"]
        self._orbit_frames = frames
        self._orbit_generation = int(getattr(self, "_orbit_generation", 0)) + 1
        return dict(frames)

    def orbit_frame(self, view: str):
        return self._orbit_frames.get(str(view))

    def orbit_generation(self) -> int:
        return int(self._orbit_generation)

    def orbit_pixel_to_world(self, view: str, u: int, v: int):
        frame = self.orbit_frame(view)
        return None if frame is None else frame.world_at(int(u), int(v))

    def hook_physics_step(self, callback):
        """Register a per-sim-step frame-capture callback for the rollout.

        LIBERO has no separate SAPIEN physics loop, but EVERY servo iteration
        calls ``step()``, so we fire ``callback`` there — giving smooth per-frame
        capture that ``_finalize_demo_video`` stitches into a demo mp4 on sim
        success. (Previously LIBERO produced NO video: the base hook was a no-op,
        so no ``tick_*.jpg`` frames were ever written.)"""
        self._tick_cb = callback
        return lambda: setattr(self, "_tick_cb", None)

    def finalize_preview(self, *, identity, category: str, media_root: Path) -> Path | None:
        """Write adapter diagnostic preview video from cached action frames.

        Called by rollout after final adjudication. This path is diagnostic-only
        and does not evaluate predicates or modify verdicts.
        """
        frames = list(self._vframes)
        steps = list(self._vframe_steps)
        if self._preview_last_frame is not None:
            if not frames:
                frames.append(self._preview_last_frame)
                steps.append(self._preview_last_timestep)
            else:
                last_step = steps[-1] if steps else None
                if self._preview_last_timestep != last_step:
                    if len(frames) < self._preview_max_frames:
                        frames.append(self._preview_last_frame)
                        steps.append(self._preview_last_timestep)
                    else:
                        frames[-1] = self._preview_last_frame
                        if steps:
                            steps[-1] = self._preview_last_timestep
        if not frames:
            return None
        import cv2 as _cv2

        out_dir = Path(media_root) / "preview" / _PREVIEW_MEDIA_DIR.get(
            category, "infrastructure"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_key = identity.key.replace("/", "__").replace(":", "__")
        out = out_dir / f"{safe_key}.mp4"
        tmp_out = out.with_name(f".{out.name}.{os.getpid()}.tmp.mp4")
        if tmp_out.exists():
            tmp_out.unlink()
        first = frames[0]
        h, w = first.shape[:2]
        writer = None
        try:
            writer = _cv2.VideoWriter(str(tmp_out), _cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
            try:
                if not bool(getattr(writer, "isOpened", lambda: True)()):
                    raise OSError(f"video writer did not open: {tmp_out}")
                for frame in frames:
                    writer.write(_cv2.cvtColor(frame, _cv2.COLOR_RGB2BGR))
            finally:
                if writer is not None:
                    writer.release()
            if (not tmp_out.exists()) or tmp_out.stat().st_size <= 0:
                raise OSError(f"encoded preview missing/empty: {tmp_out}")
            cap = _cv2.VideoCapture(str(tmp_out))
            try:
                if not bool(getattr(cap, "isOpened", lambda: False)()):
                    raise OSError(f"encoded preview not decodable: {tmp_out}")
                ok, image = cap.read()
                if not ok or image is None:
                    raise OSError(f"encoded preview has no readable frames: {tmp_out}")
            finally:
                cap.release()
            os.replace(tmp_out, out)
        except BaseException:
            if tmp_out.exists():
                tmp_out.unlink()
            raise
        return out

    # tool_handlers inherits the Env default ({}): LIBERO has no native _do_*
    # tools; the loop resolves tools only via plugin/base-skill dispatch.


class LiberoProBackend(Backend):
    """Registry entry for the public 120-task LIBERO short catalog."""

    name = "libero"

    def __init__(self, suites: tuple[str, ...] = _DEFAULT_SUITES) -> None:
        env_suites = os.environ.get("ROBORSI_LIBERO_SUITES")
        self._suites = tuple(env_suites.split(",")) if env_suites else suites

    def available(self) -> tuple[bool, str]:
        try:
            import libero.libero  # noqa: F401
            import robosuite  # noqa: F401
        except ImportError as exc:
            return False, (
                f"libero/robosuite not importable: {exc}. Run ./setup.sh to "
                "install the pinned public checkout."
            )
        return True, ""

    def _benchmark_dict(self) -> dict[str, Any]:
        try:
            from libero.libero import benchmark
        except ImportError as exc:
            raise BackendUnavailable(
                f"libero not importable: {exc}. Run ./setup.sh."
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

        # The cloud-size guards scale with image area, so 512-pixel renders do
        # not reject otherwise valid masks.
        _res = int(os.environ.get("ROBORSI_LIBERO_RES", "512"))
        cam_h = cfg.get("camera_heights", _res)
        cam_w = cfg.get("camera_widths", _res)
        controller_name = os.environ.get(
            "ROBORSI_LIBERO_CONTROLLER",
            "JOINT_POSITION",
        )
        env = OffScreenRenderEnv(
            bddl_file_name=bddl,
            controller=controller_name,
            camera_heights=cam_h,
            camera_widths=cam_w,
            camera_depths=cfg.get("camera_depths", True),  # enables unproject/pixel skills
            # Leave enough room for bounded recovery without truncating the
            # episode before the final placement attempt.
            horizon=int(os.environ.get("ROBORSI_LIBERO_HORIZON", "5000")),
        )
        env.reset()
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
        """Load the benchmark's pinned public init-state bank."""
        return bench.get_task_init_states(task_id)

    @staticmethod
    def _parse_task(task: str) -> tuple[str, int]:
        suite, _, tail = task.rpartition("/")
        if not suite:
            return task, 0                    # bare suite -> first task
        return suite, int(tail)
