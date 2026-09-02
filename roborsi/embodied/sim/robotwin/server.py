"""HTTP server for RoboTwin Backend.

Runs **inside** the RoboTwin conda env (has SAPIEN, cuRobo, lerobot). The agent
side (atomic skills, planner, judge) lives in robo-rsi main .venv and talks
to this service over HTTP.

Why HTTP: SAPIEN/cuRobo are heavy Python deps that shouldn't pollute the agent
venv. With sim-as-service, the agent venv stays clean and can swap between
sim/real by switching backend URL.

Endpoints:
  GET  /healthz
  GET  /tasks                  - list available task names
  POST /env/spawn              - body {task, config?} -> {env_id}
  POST /env/{id}/reset         - body {seed} -> msgpacked Observation
  POST /env/{id}/run_expert    - body {seed} -> msgpacked Rollout
  POST /env/{id}/run_rollout   - body {seed, instruction, expected, ...} -> msgpacked Rollout
  POST /env/{id}/step          - body {action, action_type} -> msgpacked Step
  GET  /env/{id}/obs           -> msgpacked Observation
  POST /env/{id}/close

Body / response wire format: msgpack with ``msgpack-numpy`` patches so
``ndarray`` round-trips natively (avoids base64 in JSON for images).
"""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from typing import Any

import msgpack
import msgpack_numpy
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from roborsi.embodied.agent_loop.env import (
    BackendUnavailable, Env, Observation, Rollout, Step,
)
from roborsi.embodied.sim.robotwin.adapter import RoboTwinBackend


msgpack_numpy.patch()      # makes np.ndarray msgpack-encodable

app = FastAPI(title="roborsi-sim", version="0.1.0")
_backend = RoboTwinBackend()
_envs: dict[str, Env] = {}


def _pack(obj: Any) -> bytes:
    return msgpack.packb(_to_dict(obj), use_bin_type=True)


def _to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


def _msgpack_response(obj: Any) -> Response:
    return Response(content=_pack(obj), media_type="application/msgpack")


def _get_env(env_id: str) -> Env:
    env = _envs.get(env_id)
    if env is None:
        raise HTTPException(404, f"env_id '{env_id}' not found")
    return env


# ────────────────────────────────────────────────────────────────────────


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    ok, reason = _backend.available()
    return {"ok": ok, "reason": reason, "active_envs": list(_envs.keys())}


@app.get("/tasks")
def tasks() -> dict[str, Any]:
    return {"tasks": _backend.list_tasks()}


@app.post("/env/spawn")
def spawn(body: dict[str, Any]) -> dict[str, str]:
    task = body.get("task")
    if not task:
        raise HTTPException(400, "task required")
    config = body.get("config") or {}
    env = _backend.make_env(task, config)
    env_id = uuid.uuid4().hex[:12]
    _envs[env_id] = env
    return {"env_id": env_id, "task": task}


@app.post("/env/{env_id}/reset")
def reset(env_id: str, body: dict[str, Any]) -> Response:
    env = _get_env(env_id)
    obs = env.reset(int(body.get("seed", 0)))
    return _msgpack_response(obs)


@app.post("/env/{env_id}/run_expert")
def run_expert(env_id: str, body: dict[str, Any]) -> Response:
    env = _get_env(env_id)
    rollout = env.run_expert(int(body.get("seed", 0)))
    return _msgpack_response(rollout)


@app.post("/env/{env_id}/run_rollout")
def run_rollout(env_id: str, body: dict[str, Any]) -> Response:
    env = _get_env(env_id)
    workdir = body.get("workdir")
    rollout = env.run_rollout(
        seed=int(body.get("seed", 0)),
        instruction=str(body.get("instruction", "")),
        expected_on_success=str(body.get("expected_on_success", "")),
        model=body.get("model"),
        tool_budget=int(body.get("tool_budget", 25)),
        workdir=Path(workdir) if workdir else None,
    )
    return _msgpack_response(rollout)


@app.post("/env/{env_id}/step")
def step(env_id: str, body: dict[str, Any]) -> Response:
    env = _get_env(env_id)
    sim_step = env.step(body["action"], action_type=body.get("action_type", "qpos"))
    return _msgpack_response(sim_step)


@app.get("/env/{env_id}/obs")
def obs(env_id: str) -> Response:
    env = _get_env(env_id)
    impl = getattr(env, "_impl", None)
    if impl is None:
        return _msgpack_response(Observation())
    from roborsi.embodied.sim.robotwin.adapter import _to_sim_obs
    return _msgpack_response(_to_sim_obs(impl.get_obs()))


@app.post("/env/{env_id}/close")
def close(env_id: str) -> dict[str, Any]:
    env = _envs.pop(env_id, None)
    if env is not None:
        env.close()
    return {"closed": env_id}
