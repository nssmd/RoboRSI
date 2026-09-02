"""HTTP client for remote RoboTwin Backend.

Lives on the agent side (robo-rsi main .venv). Speaks to a server (see
server.py) running inside the RoboTwin conda env. The agent venv stays clean
of SAPIEN / cuRobo / torch+cu118 deps — only ``requests`` + ``msgpack`` here.

Usage::

    backend = get_backend("robotwin-http")    # reads ROBORSI_SIM_URL
    env = backend.make_env("click_bell")
    rollout = env.run_rollout(seed=1, instruction="...", expected_on_success="...")
    env.close()

The wire format is msgpack with ``msgpack-numpy`` patches so np.ndarray
images / qpos round-trip natively. Same machine: localhost; cross-machine:
set ROBORSI_SIM_URL=http://host:8181.
"""

from __future__ import annotations

import os
from typing import Any

import msgpack
import msgpack_numpy
import requests

from roborsi.embodied.agent_loop.env import (
    Backend, BackendUnavailable, Env,
    Observation, Rollout, Step,
)


msgpack_numpy.patch()

DEFAULT_URL = os.environ.get("ROBORSI_SIM_URL", "http://localhost:8181")


# ────────────────────────────────────────────────────────────────────────
# wire-format helpers
# ────────────────────────────────────────────────────────────────────────


def _unpack(content: bytes) -> Any:
    return msgpack.unpackb(content, raw=False)


def _to_obs(d: dict[str, Any]) -> Observation:
    return Observation(
        images=d.get("images") or {},
        state=d.get("state"),
        timestamp=float(d.get("timestamp") or 0.0),
        extras=d.get("extras") or {},
    )


def _to_step(d: dict[str, Any]) -> Step:
    return Step(
        obs=_to_obs(d.get("obs") or {}),
        action=d.get("action"),
        reward=float(d.get("reward") or 0.0),
        done=bool(d.get("done", False)),
        info=d.get("info") or {},
    )


def _to_rollout(d: dict[str, Any]) -> Rollout:
    rollout = Rollout(
        task=str(d.get("task", "")),
        seed=int(d.get("seed", -1)),
        steps=[_to_step(s) for s in (d.get("steps") or [])],
        success=bool(d.get("success", False)),
        outcome=str(d.get("outcome", "")),
        meta=d.get("meta") or {},
    )
    return rollout


# ────────────────────────────────────────────────────────────────────────
# Client classes
# ────────────────────────────────────────────────────────────────────────


class HttpRobotwinEnv(Env):
    """Remote Env proxy. Each method = one HTTP call."""

    backend_name = "robotwin-http"

    def __init__(self, base_url: str, env_id: str, task: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.env_id = env_id
        self.task = task

    def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        r = requests.post(f"{self.base_url}{path}", json=body or {}, timeout=600)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/msgpack"):
            return _unpack(r.content)
        return r.json()

    def _get(self, path: str) -> Any:
        r = requests.get(f"{self.base_url}{path}", timeout=120)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/msgpack"):
            return _unpack(r.content)
        return r.json()

    def reset(self, seed: int) -> Observation:
        return _to_obs(self._post(f"/env/{self.env_id}/reset", {"seed": int(seed)}))

    def run_expert(self, seed: int) -> Rollout:
        return _to_rollout(self._post(f"/env/{self.env_id}/run_expert", {"seed": int(seed)}))

    def run_rollout(
        self,
        seed: int,
        instruction: str,
        expected_on_success: str,
        model: str | None = None,
        tool_budget: int = 25,
        workdir: Any = None,
    ) -> Rollout:
        return _to_rollout(self._post(f"/env/{self.env_id}/run_rollout", {
            "seed": int(seed),
            "instruction": instruction,
            "expected_on_success": expected_on_success,
            "model": model,
            "tool_budget": int(tool_budget),
            "workdir": str(workdir) if workdir else None,
        }))

    def step(self, action, action_type: str = "qpos") -> Step:
        return _to_step(self._post(f"/env/{self.env_id}/step", {
            "action": action.tolist() if hasattr(action, "tolist") else action,
            "action_type": action_type,
        }))

    def close(self) -> None:
        try:
            self._post(f"/env/{self.env_id}/close")
        except requests.RequestException:
            pass


class HttpRobotwinBackend(Backend):
    name = "robotwin-http"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")

    def _check_alive(self) -> None:
        try:
            r = requests.get(f"{self.base_url}/healthz", timeout=3)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok", True):
                raise BackendUnavailable(
                    f"sim server at {self.base_url} reports unavailable: "
                    f"{data.get('reason')}"
                )
        except requests.RequestException as exc:
            raise BackendUnavailable(
                f"cannot reach sim server at {self.base_url}: {exc}. "
                f"Start it with `scripts/roborsi-sim-server`."
            )

    def list_tasks(self) -> list[str]:
        self._check_alive()
        r = requests.get(f"{self.base_url}/tasks", timeout=10)
        r.raise_for_status()
        return list(r.json().get("tasks") or [])

    def make_env(self, task: str, config: dict[str, Any] | None = None) -> Env:
        self._check_alive()
        r = requests.post(
            f"{self.base_url}/env/spawn",
            json={"task": task, "config": config or {}},
            timeout=120,
        )
        r.raise_for_status()
        env_id = r.json()["env_id"]
        return HttpRobotwinEnv(self.base_url, env_id, task)
