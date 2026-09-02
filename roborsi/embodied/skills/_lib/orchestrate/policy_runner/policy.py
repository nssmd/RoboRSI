"""_lib.orchestrate.policy_runner — load + run LeRobot policies on Env."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from roborsi.embodied.agent_loop.env import Env, Observation


def load_policy(checkpoint: str):
    """Load a LeRobot-trained policy (pi0/ACT/DP/...) from a local checkpoint dir.

    Reads `train_config.json` next to `pretrained_model/` to bind dataset
    metadata. Sends to CUDA + eval. Same logic the success_rate eval uses.
    """
    from lerobot.policies.factory import make_policy
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata

    cfg = PreTrainedConfig.from_pretrained(checkpoint)
    train_cfg_path = Path(checkpoint) / "train_config.json"
    if not train_cfg_path.exists():
        raise RuntimeError(
            f"Cannot load policy from {checkpoint}: no sidecar train_config.json"
        )
    train_cfg = json.loads(train_cfg_path.read_text(encoding="utf-8"))
    ds_block = train_cfg.get("dataset", {})
    repo_id, ds_root = ds_block.get("repo_id"), ds_block.get("root")
    if not repo_id or not ds_root:
        raise RuntimeError(f"train_config.json missing dataset info at {checkpoint}")
    ds_meta = LeRobotDatasetMetadata(repo_id=repo_id, root=ds_root)

    policy = make_policy(cfg=cfg, ds_meta=ds_meta)
    policy.eval()
    return policy


def policy_forward(policy, obs: Observation, action_type: str = "qpos"):
    """Observation → batch → policy.select_action → numpy."""
    import torch
    device = next(policy.parameters()).device
    batch: dict[str, Any] = {}
    for cam, img in (obs.images or {}).items():
        if img is None:
            continue
        arr = np.asarray(img).astype("float32") / 255.0
        if arr.ndim == 3:
            arr = np.transpose(arr, (2, 0, 1))
        batch[f"observation.images.{cam}"] = torch.from_numpy(arr).unsqueeze(0).to(device)
    if obs.state is not None:
        state = _flatten_state(obs.state)
        batch["observation.state"] = torch.from_numpy(state).unsqueeze(0).to(device)
    out = policy.select_action(batch)
    if isinstance(out, torch.Tensor):
        return out.detach().cpu().numpy().squeeze()
    return np.asarray(out).squeeze()


def rollout_one(
    policy,
    env: Env,
    seed: int | None = None,
    max_steps: int = 200,
    action_type: str = "qpos",
    reset_first: bool = True,
) -> dict[str, Any]:
    """Reset (optional) + step the env until done or `max_steps`."""
    obs = env.reset(seed) if reset_first and seed is not None else _peek_obs(env)
    done = False
    steps = 0
    while not done and steps < max_steps:
        action = policy_forward(policy, obs, action_type)
        step = env.step(action, action_type=action_type)
        obs = step.obs
        done = step.done
        steps += 1
    success = _check_env_success(env) or done
    return {
        "seed": seed,
        "success": bool(success),
        "outcome": "success" if success else ("done" if done else "timeout"),
        "steps": steps,
    }


def run(**_: Any) -> dict[str, Any]:
    """No-op skill entry; consumers import the helpers above directly."""
    return {"skill": "policy_runner", "note": "import-only library"}


def _flatten_state(state: Any) -> np.ndarray:
    if isinstance(state, dict):
        parts = []
        for k in ("left_arm", "left_gripper", "right_arm", "right_gripper"):
            v = state.get(k)
            if v is None:
                continue
            parts.append(np.asarray(v, dtype="float32").flatten())
        if parts:
            return np.concatenate(parts)
    if hasattr(state, "tolist"):
        return np.asarray(state, dtype="float32").flatten()
    return np.asarray(state, dtype="float32").flatten()


def _peek_obs(env: Env) -> Observation:
    impl = getattr(env, "_impl", None)
    if impl is None:
        return Observation()
    from roborsi.embodied.sim.robotwin.adapter import _to_sim_obs
    return _to_sim_obs(impl.get_obs())


def _check_env_success(env: Env) -> bool:
    impl = getattr(env, "_impl", None)
    if impl is None:
        return False
    fn = getattr(impl, "check_success", None) or getattr(impl, "_check_success", None)
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False
