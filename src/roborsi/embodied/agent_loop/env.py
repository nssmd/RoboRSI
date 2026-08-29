"""Backend-agnostic environment contract for the universal VLM tool-loop.

An ``Env`` is a disposable per-episode handle over ANY embodiment — a LIBERO
sim task, a LIBERO kitchen, or (future) a real robot. The ``run_rollout``
driver in ``agent_loop.rollout`` operates purely against this contract, so the
SAME tool-loop logic serves every backend. Backend-specific machinery (SAPIEN
scene, cuRobo, robosuite, real-robot drivers) lives behind the seam methods
below and never leaks into the loop.

The surface is deliberately small: reset + expert playback + close for data
collection, plus four *seam* methods the loop needs to stay environment-blind:
``take_snapshot`` / ``check_success`` / ``hook_physics_step`` / ``tool_handlers``.
A ``Backend`` is a named factory that builds per-task ``Env`` instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


class BackendUnavailable(RuntimeError):
    """Raised when a backend is requested but its deps are missing."""


@dataclass
class Observation:
    """Snapshot from the environment at one step.

    Field layout deliberately mirrors LeRobot: ``images`` is a dict keyed by
    camera name (``front``, ``head``, ``left_wrist``, ``right_wrist``, ...),
    each value an HWC uint8 RGB ndarray. ``state`` is the proprioceptive
    vector (qpos / endpose). ``extras`` carries anything backend-specific.
    """

    images: dict[str, Any] = field(default_factory=dict)
    state: Any = None
    timestamp: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """One (obs, action, reward, done) tuple recorded during an episode."""

    obs: Observation
    action: Any = None
    reward: float = 0.0
    done: bool = False
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rollout:
    """An expert or policy rollout over one episode."""

    task: str
    seed: int
    steps: list[Step] = field(default_factory=list)
    success: bool = False
    outcome: str = ""                # "success" | "failure" | "aborted"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.steps)


class Env(ABC):
    """Per-task handle. One instance = one task setup; reset() reseeds."""

    task: str
    backend_name: str

    @abstractmethod
    def reset(self, seed: int) -> Observation: ...

    @abstractmethod
    def run_expert(self, seed: int) -> Rollout:
        """Run the task's built-in scripted expert to completion.

        The backend is responsible for invoking whatever script ships with
        the environment (e.g. LIBERO's ``play_once``) and capturing step
        tuples into a ``Rollout``.
        """

    def step(self, action, action_type: str = "qpos") -> Step:
        """Advance one step with ``action``.

        Default raises NotImplementedError — not every backend supports
        closed-loop per-step execution. Backends that do override this.
        ``action_type`` accepts ``qpos`` (joint targets) or ``ee``
        (end-effector pose + gripper), aligning with LIBERO's
        ``take_action`` and LeRobot's policy output modes.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.step() not implemented. "
            "Learned-policy eval / vlm collection need it."
        )

    @abstractmethod
    def close(self) -> None: ...

    # ── Seam methods for the universal rollout loop ──────────────────────
    # run_rollout touches ONLY these — never env._impl / env._env. Each
    # backend that drives the local loop maps them onto its own internals.
    # Kept non-abstract (like step()) so remote/proxy Envs that never drive
    # the loop locally stay instantiable.

    def take_snapshot(self) -> Observation:
        """Fresh observation, valid pre/post step. Replaces the old
        module-level ``_snapshot(env)``. Backends that drive the local
        rollout loop MUST override this."""
        raise NotImplementedError(
            f"{type(self).__name__}.take_snapshot() not implemented — "
            "required to drive the universal rollout loop locally."
        )

    def check_success(self) -> bool | None:
        """Ground-truth task-success predicate, or None if the backend has
        none (e.g. real robot / open-ended task). Default: None."""
        return None

    def hook_physics_step(
        self, on_tick: Callable[..., None]
    ) -> Callable[[], None]:
        """Register a callback for each simulated physics tick.

        Backends may pass the exact low-level controller action as the first
        argument. Backends without access to that action may call with no
        arguments. The loop handles both forms and subsamples observations.
        """
        return lambda: None

    def tool_handlers(self) -> dict[str, Callable]:
        """Return this backend's ``{name: handler(ctx, args)}`` tool map (the
        ``_do_<name>`` implementations). Default: empty — a backend with no
        native tools relies solely on plugin/base-skill dispatch."""
        return {}

    def __enter__(self) -> "Env":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Backend(ABC):
    """Registry entry. Factory for Env instances."""

    name: str

    @abstractmethod
    def list_tasks(self) -> list[str]: ...

    @abstractmethod
    def make_env(self, task: str, config: dict[str, Any] | None = None) -> Env: ...

    def available(self) -> tuple[bool, str]:
        """Return (ok, reason). Default: try list_tasks(); catch availability errors."""
        try:
            self.list_tasks()
        except BackendUnavailable as exc:
            return False, str(exc)
        return True, ""
