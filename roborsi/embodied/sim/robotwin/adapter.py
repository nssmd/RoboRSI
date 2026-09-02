"""RoboTwin 2.0 adapter.

RoboTwin ships as a standalone repo with its own conda env (Python 3.10,
CUDA 12.1, SAPIEN, cuRobo). We don't pip-install it into robo-cli — that
would drag a massive dep tree onto every machine. Instead the adapter
expects RoboTwin to live at:

    $ROBORSI_ROBOTWIN_ROOT  (env var)
    $ROBORSI_ROBOTWIN_ROOT    (backward-compat env var during rename)
    ~/RoboTwin                 (default)

and dynamically prepends that path to ``sys.path`` before importing its
``envs.<task>`` module. This keeps the package boundary clean while still
giving us a native Python API (no subprocess latency).

Task execution uses the expert shipped in each task's ``play_once()``.
Observations are captured by wrapping ``get_obs()`` around each step.

Coverage note: RoboTwin's task classes differ slightly from the public
gym.Env contract — ``step()`` is not universal. We deliberately only call
``play_once()`` and ``get_obs()``; the Env.step() surface is left
unimplemented until we need closed-loop policy inference inside sim.
"""

from __future__ import annotations

import os
import sys
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


def _resolve_root() -> Path:
    _bicoord = os.environ.get("ROBORSI_BICOORD_ROOT")
    candidates = [
        os.environ.get("ROBORSI_ROBOTWIN_ROOT"),
        # RoboTwin normally sits beside its BiCoord-Bench fork — derive the
        # sibling from the configured BiCoord root instead of hardcoding it.
        (str(Path(_bicoord).expanduser().parent / "RoboTwin") if _bicoord else None),
        str(Path.home() / "RoboTwin"),
    ]
    for c in candidates:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.exists():
            return p
    raise BackendUnavailable(
        "RoboTwin not found. Clone https://github.com/RoboTwin-Platform/RoboTwin "
        "to ~/RoboTwin or set ROBORSI_ROBOTWIN_ROOT."
    )


def _ensure_on_path(root: Path) -> None:
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)


def _lazy_import_task(root: Path, task: str):
    """Import RoboTwin's ``envs.<task>`` module dynamically."""
    _ensure_on_path(root)
    module_name = f"envs.{task}"
    import importlib
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise BackendUnavailable(
            f"RoboTwin task module '{module_name}' not importable: {exc}. "
            f"Check {root}/envs/{task}.py exists and its deps (sapien, cuRobo, "
            f"torch) are installed in the active python env."
        ) from exc
    # RoboTwin convention: class name == filename (snake_case, lowercase).
    cls = getattr(module, task, None)
    if cls is None or not isinstance(cls, type):
        # Fallback 1: CamelCased / PascalCased variants
        candidates = {task.replace("_", "").lower(), task.lower()}
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and attr.lower() in candidates:
                cls = obj
                break
    if cls is None:
        # Fallback 2: first class declared in this module
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and obj.__module__ == module_name:
                cls = obj
                break
    if cls is None:
        raise BackendUnavailable(f"no Task class found in {module_name}")
    return cls


def _extract_images(obs: dict[str, Any]) -> dict[str, Any]:
    """RoboTwin obs['observation']['<cam>']['rgb'] → {cam: ndarray}."""
    images: dict[str, Any] = {}
    node = obs.get("observation") if isinstance(obs, dict) else None
    if not isinstance(node, dict):
        return images
    for cam, payload in node.items():
        if isinstance(payload, dict) and "rgb" in payload:
            images[cam] = payload["rgb"]
    return images


def _extract_state(obs: dict[str, Any]) -> Any:
    """Return a flat numpy float32 vector of the proprioceptive state.

    RoboTwin's ``get_obs`` wraps qpos under ``joint_action`` (sub-fields
    ``left_arm``, ``left_gripper``, ``right_arm``, ``right_gripper``,
    ``vector``). We use ``vector`` directly when present (already concatenated)
    and fall back to assembling the parts otherwise.
    """
    import numpy as np
    if not isinstance(obs, dict):
        return None
    ja = obs.get("joint_action") or obs.get("endpose") or obs.get("qpos")
    if ja is None:
        return None
    if isinstance(ja, dict):
        if "vector" in ja and ja["vector"] is not None:
            return np.asarray(ja["vector"], dtype=np.float32).flatten()
        parts = []
        for key in ("left_arm", "left_gripper", "right_arm", "right_gripper"):
            v = ja.get(key)
            if v is None:
                continue
            arr = np.asarray(v, dtype=np.float32).flatten()
            parts.append(arr)
        if parts:
            return np.concatenate(parts)
        return None
    return np.asarray(ja, dtype=np.float32).flatten()


def _flatten_action(action) -> Any:
    import numpy as np
    if action is None:
        return None
    return np.asarray(action, dtype=np.float32).flatten()


class RoboTwinBackend(Backend):
    """Registry entry for RoboTwin 2.0 (and forks like BiCoord-Bench).

    The ``task_root`` argument lets us point the same adapter at different
    repos that share RoboTwin's API surface. Defaults to RoboTwin via env vars.
    """

    name = "robotwin"

    def __init__(self, task_root: Path | str | None = None) -> None:
        self._root: Path | None = Path(task_root).expanduser() if task_root else None

    def _root_or_raise(self) -> Path:
        if self._root is None:
            self._root = _resolve_root()
        return self._root

    def list_tasks(self) -> list[str]:
        root = self._root_or_raise()
        envs_dir = root / "envs"
        if not envs_dir.is_dir():
            raise BackendUnavailable(f"{envs_dir} does not exist")
        tasks: list[str] = []
        for p in envs_dir.iterdir():
            if p.suffix != ".py":
                continue
            if p.stem.startswith("_"):
                continue
            tasks.append(p.stem)
        return sorted(tasks)

    def make_env(self, task: str, config: dict[str, Any] | None = None) -> Env:
        root = self._root_or_raise()
        return RoboTwinEnv(root=root, task=task, config=config or {})


class RoboTwinEnv(Env):
    """One RoboTwin task environment."""

    backend_name = "robotwin"

    def __init__(self, root: Path, task: str, config: dict[str, Any]) -> None:
        self.root = root
        self.task = task
        self.config = config
        self._task_cls = _lazy_import_task(root, task)
        self._impl: Any = None    # instance of RoboTwin task class
        self._last_obs: Observation | None = None

    def _init_impl(self, seed: int) -> Any:
        impl = self._task_cls()
        # RoboTwin's _init_task_env_ reads .get() on 'domain_randomization' so
        # the key must be a dict even if we want everything off. Same for
        # data_type: callers can override via self.config.
        init_kwargs = _build_task_kwargs(
            root=self.root,
            task=self.task,
            seed=seed,
            user_config=self.config,
        )
        init = getattr(impl, "setup_demo", None) or getattr(impl, "_init_task_env_", None)
        if init is None:
            raise BackendUnavailable(
                f"task '{self.task}' exposes neither setup_demo nor _init_task_env_"
            )
        try:
            init(**init_kwargs)
        except Exception:
            # setup_demo may raise UnStableError mid-build (objects spawn
            # interpenetrating). Tear down the half-built sapien scene before
            # propagating so the resample loop in reset() doesn't leak the
            # failed scene's GPU resources across retries.
            closer = getattr(impl, "close_env", None) or getattr(impl, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
            raise
        return impl

    def reset(self, seed: int) -> Observation:
        # RoboTwin's setup_demo raises UnStableError when actors spawn
        # interpenetrating/unstable (e.g. two bottles in pick_diverse_bottles,
        # shoes in place_dual_shoes). This is a SCENE-INIT physics failure — the
        # episode never starts, so no prompt/seed tweak at the run layer can fix
        # it. EVERY BiCoord harness (eval_policy/check/collect_data) handles it
        # the same way: resample with an incremented seed until the scene is
        # stable. Our adapter previously called _init_impl ONCE, so an unstable
        # spawn killed the whole task. Mirror BiCoord's canonical resample here.
        try:
            from envs.utils.create_actor import UnStableError
        except Exception:
            UnStableError = ()          # nothing to catch if unimportable
        last_err: Exception | None = None
        for attempt in range(12):
            try:
                self._impl = self._init_impl(seed + attempt)
                self._snapshot_predicate_refs()
                obs = self._impl.get_obs()
                self._last_obs = _to_sim_obs(obs)
                return self._last_obs
            except Exception as e:
                is_unstable = (UnStableError and isinstance(e, UnStableError)) \
                    or type(e).__name__ == "UnStableError"
                if not is_unstable:
                    raise
                last_err = e               # resample on the next seed
        raise last_err

    def step(self, action, action_type: str = "qpos") -> Step:
        """Advance one step via RoboTwin's ``take_action``.

        ``action`` shape:
          - action_type='qpos': [left_arm_dim, left_gripper, right_arm_dim, right_gripper]
            (arm dims depend on embodiment; aloha = 6+1+6+1 = 14)
          - action_type='ee':  [left_ee_7, left_gripper, right_ee_7, right_gripper]
        """
        if self._impl is None:
            raise RuntimeError("step() before reset()")
        take = getattr(self._impl, "take_action", None)
        if take is None:
            raise NotImplementedError(
                f"task '{self.task}' has no take_action; RoboTwin version too old?"
            )
        import numpy as np
        take(np.asarray(action, dtype=np.float64), action_type=action_type)
        obs = self._impl.get_obs()
        sim_obs = _to_sim_obs(obs)
        self._last_obs = sim_obs
        reward = 0.0
        done = bool(getattr(self._impl, "eval_success", False))
        checker = getattr(self._impl, "check_success", None) or getattr(
            self._impl, "_check_success", None
        )
        if checker is not None:
            done = bool(checker()) or done
        info = {"step_cnt": getattr(self._impl, "take_action_cnt", None)}
        return Step(obs=sim_obs, action=action, reward=reward, done=done, info=info)

    def run_expert(self, seed: int) -> Rollout:
        """Drive the task's ``play_once`` expert; capture per-tick (obs, qpos)."""
        self.reset(seed)
        rollout = Rollout(task=self.task, seed=seed)
        if self._last_obs is not None:
            rollout.steps.append(Step(obs=self._last_obs, info={"phase": "reset"}))

        play = getattr(self._impl, "play_once", None)
        if play is None:
            raise BackendUnavailable(f"task '{self.task}' has no play_once()")

        # Hook scene.step() so every physics tick lands an (obs, action) tuple.
        # action = next-tick qpos (absolute target; lerobot/pi0 imitation friendly).
        impl = self._impl
        scene = impl.scene
        original_step = scene.step
        subsample_every = int(self.config.get("step_subsample", 5))
        tick_counter = {"n": 0}

        def _hooked_step():
            result = original_step()
            tick_counter["n"] += 1
            if tick_counter["n"] % subsample_every != 0:
                return result
            obs = impl.get_obs()
            sim_obs = _to_sim_obs(obs)
            rollout.steps.append(Step(
                obs=sim_obs,
                action=sim_obs.state,        # absolute next-tick qpos
                info={"tick": tick_counter["n"]},
            ))
            return result

        scene.step = _hooked_step
        start = time.time()
        # BiCoord/RoboTwin's play_once can raise AssertionError when the
        # randomly-sampled scene exceeds what its scripted plan can solve
        # (e.g. place_actor returns a None pose). Treat that seed as a
        # failed expert run and move on rather than killing the batch.
        play_error: str | None = None
        try:
            play()
        except (AssertionError, ValueError) as exc:
            play_error = f"{type(exc).__name__}: {exc}"
            impl.plan_success = False
        scene.step = original_step

        final_obs = _to_sim_obs(impl.get_obs())
        rollout.steps.append(Step(obs=final_obs, info={"phase": "final"}))

        success = False
        checker = getattr(impl, "check_success", None) or getattr(impl, "_check_success", None)
        if checker is not None:
            success = bool(checker())
        rollout.success = success
        rollout.outcome = "success" if success else ("failure_play_error" if play_error else "failure")
        rollout.meta = {
            "backend": self.backend_name,
            "wall_time_s": round(time.time() - start, 3),
            "ticks": tick_counter["n"],
            "subsample_every": subsample_every,
            "play_error": play_error,
        }
        return rollout

    def close(self) -> None:
        if self._impl is None:
            return
        closer = getattr(self._impl, "close_env", None) or getattr(
            self._impl, "close", None
        )
        if closer is not None:
            closer()
        self._impl = None

    # ── Seam methods for the universal rollout loop ──────────────────────

    def take_snapshot(self) -> Observation:
        """Fresh obs from the RoboTwin impl (valid pre/post step)."""
        return _to_sim_obs(self._impl.get_obs())

    def check_success(self) -> bool | None:
        """RoboTwin's ground-truth predicate. None if the task exposes none.
        Lets exceptions propagate (repo rule: no silent swallow)."""
        fn = getattr(self._impl, "check_success", None) or getattr(
            self._impl, "_check_success", None
        )
        if fn is None:
            return None
        self._ensure_arm_tag()
        return bool(fn())

    def _ensure_arm_tag(self) -> None:
        """Some task check_success predicates read ``self.arm_tag`` to pick which
        gripper's release to verify. It is normally set only in the GT
        ``play_once``, which the pure-vision rollout skips — so the predicate
        would ``AttributeError``. Supply the FAITHFUL value: the last arm to
        close its gripper (the grasping / placing arm, recorded by _do_gripper).
        Only sets it when the impl both needs it and lacks it — never overrides a
        value the task set, and never weakens the predicate (if that arm is still
        holding, its gripper is closed → the predicate correctly fails)."""
        impl = self._impl
        if getattr(impl, "arm_tag", None) is not None:
            return
        arm = getattr(impl, "_rh_last_close_arm", None)
        if arm is None:
            return
        from envs.utils.action import ArmTag
        impl.arm_tag = ArmTag(arm)

    def _snapshot_predicate_refs(self) -> None:
        """Capture the episode-START reference state some task check_success
        predicates read but only the GT ``play_once`` sets — the pure-vision
        rollout skips play_once, so the predicate would ``AttributeError``.
        Snapshotting the manipulated actor's OWN initial pose is a legitimate
        observable (perceivable at episode start); it feeds only the sim
        predicate's reference, never the agent's perception pipeline.

        origin_z: put_object_cabinet.check_success (and peers) require the object
        to rise a bounded amount above ``self.origin_z`` — its z BEFORE
        manipulation. Set it from the object's initial z exactly as play_once
        (``self.origin_z = self.object.get_pose().p[2]``) does."""
        impl = self._impl
        obj = getattr(impl, "object", None)
        if obj is None or getattr(impl, "origin_z", None) is not None:
            return
        get_pose = getattr(obj, "get_pose", None)
        if get_pose is not None:
            impl.origin_z = float(get_pose().p[2])

    def tool_handlers(self) -> dict[str, Any]:
        """name → _do_<name>(ctx, args) map. Delegates to robotwin_agent's
        memoized registry so introspection callers share one source."""
        from roborsi.embodied.sim.robotwin.robotwin_agent import _ensure_registry
        return _ensure_registry()

    def hook_physics_step(self, on_tick):
        """Wire ``on_tick()`` into SAPIEN's ``scene.step`` so the loop can
        capture a dense per-tick trajectory during tool execution. Returns an
        unhook callable that restores the original stepper."""
        scene = self._impl.scene
        original_step = scene.step

        def _hooked_step():
            result = original_step()
            on_tick()
            return result

        scene.step = _hooked_step

        def _unhook() -> None:
            scene.step = original_step

        return _unhook

    def run_rollout(
        self,
        seed: int,
        instruction: str,
        expected_on_success: str,
        model: str | None = None,
        tool_budget: int = 25,
        workdir: Any = None,
    ) -> Rollout:
        """VLM-driven episode (no scripted expert).

        Uses ``roborsi.embodied.sim.robotwin.robotwin_agent`` which
        captures one step per tool call, so the resulting Rollout has
        dense per-action obs unlike ``run_expert``'s start+end snapshot.
        """
        # Force depth-enabled config so the unprojection has data to chew.
        self.config = dict(self.config)
        self.config["require_depth"] = True
        self.reset(seed)
        from roborsi.embodied.agent_loop.rollout import run_rollout
        result = run_rollout(
            self,
            seed=seed,
            task_name=self.task,
            instruction=instruction,
            expected_on_success=expected_on_success,
            model=model,
            tool_budget=tool_budget,
            workdir=workdir,
        )
        # Persist VLM tool-call trace to the rollout meta so callers (zeroshot
        # subskills) and the DataStore have full visibility.
        result.rollout.meta["vlm_trace"] = result.trace
        return result.rollout


def _to_sim_obs(raw: dict[str, Any] | None) -> Observation:
    if raw is None:
        return Observation()
    return Observation(
        images=_extract_images(raw),
        state=_extract_state(raw),
        timestamp=time.time(),
        extras={k: v for k, v in raw.items() if k not in {"observation"}} if isinstance(raw, dict) else {},
    )


def _build_task_kwargs(
    root: Path,
    task: str,
    seed: int,
    user_config: dict[str, Any],
) -> dict[str, Any]:
    """Replicate ``script/collect_data.py:main()`` argument assembly.

    RoboTwin tasks need ``left_embodiment_config`` / ``right_embodiment_config``
    plus camera + data_type + domain_randomization dicts. Default values are
    sourced from ``task_config/demo_clean.yml``; user_config overlays per-call.
    """
    import yaml
    base_config_name = user_config.get("task_config", "demo_clean")
    config_path = root / "task_config" / f"{base_config_name}.yml"
    if not config_path.exists():
        raise BackendUnavailable(f"task config '{config_path}' not found")
    with config_path.open("r", encoding="utf-8") as f:
        args: dict[str, Any] = yaml.safe_load(f) or {}

    args["task_name"] = task
    args["seed"] = seed
    args["now_ep_num"] = user_config.get("now_ep_num", 0)

    # Force depth on if caller asked for rollout mode (needs unprojection).
    if user_config.get("require_depth"):
        args.setdefault("data_type", {})
        if isinstance(args["data_type"], dict):
            args["data_type"]["depth"] = True
            args["data_type"]["rgb"] = True

    # Embodiments
    embodiment_type = user_config.get("embodiment") or args.get("embodiment") or ["aloha-agilex"]
    embodiment_config_path = root / "task_config" / "_embodiment_config.yml"
    with embodiment_config_path.open("r", encoding="utf-8") as f:
        embodiment_types = yaml.safe_load(f) or {}

    def _resolve(name: str) -> str:
        spec = embodiment_types.get(name)
        if not spec or "file_path" not in spec:
            raise BackendUnavailable(f"unknown embodiment '{name}'")
        rel = spec["file_path"]
        return str((root / rel).resolve()) if not Path(rel).is_absolute() else rel

    if len(embodiment_type) == 1:
        args["left_robot_file"] = _resolve(embodiment_type[0])
        args["right_robot_file"] = _resolve(embodiment_type[0])
        args["dual_arm_embodied"] = True
    elif len(embodiment_type) == 3:
        args["left_robot_file"] = _resolve(embodiment_type[0])
        args["right_robot_file"] = _resolve(embodiment_type[1])
        args["embodiment_dis"] = embodiment_type[2]
        args["dual_arm_embodied"] = False
    else:
        raise BackendUnavailable(
            f"embodiment must have 1 or 3 entries, got {embodiment_type!r}"
        )

    args["left_embodiment_config"] = _load_embodiment_config(args["left_robot_file"])
    args["right_embodiment_config"] = _load_embodiment_config(args["right_robot_file"])

    # Render off by default; collectors that want a viewer set render_freq via user_config.
    args["render_freq"] = user_config.get("render_freq", args.get("render_freq", 0))
    args["save_data"] = False
    # Apply user overrides last (escape hatch).
    for k, v in (user_config.get("init_overrides") or {}).items():
        args[k] = v
    return args


def _load_embodiment_config(robot_file: str) -> dict[str, Any]:
    import yaml
    cfg_path = Path(robot_file) / "config.yml"
    if not cfg_path.exists():
        raise BackendUnavailable(f"embodiment config '{cfg_path}' missing")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _bump_tiny_actor_masses(impl, min_mass_kg: float = 0.05) -> None:
    """RoboTwin sometimes sets dynamic actor mass to 0.001kg (1g) for
    tasks like beat_block_hammer — unrealistic and physics-unstable. Bump
    any dynamic actor below `min_mass_kg` up to it, post-setup. Affects
    physics only; geometry and check_success unchanged."""
    import sapien
    bumped = []
    for actor in impl.scene.get_all_actors():
        for comp in actor.get_components():
            if isinstance(comp, sapien.physx.PhysxRigidDynamicComponent):
                if comp.mass < min_mass_kg:
                    old = comp.mass
                    comp.mass = min_mass_kg
                    bumped.append((actor.get_name(), old, min_mass_kg))
                break
    if bumped:
        import sys
        for n, old, new in bumped:
            sys.stderr.write(f"[adapter] bumped {n} mass {old:.4f} → {new}kg\n")
