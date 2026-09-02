"""evaluation.success_rate — held-out seed evaluation.

Executors:
- ``expert``: drive backend's ``run_expert`` (baseline).
- ``pi0_checkpoint``: load a LeRobot policy checkpoint, roll it out
  step-by-step via ``Env.step(action)``.
- ``rollout_vlm``: placeholder (awaits SimActionToolGroup).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.agent_loop.env import Env, Observation


def _default_eval_root() -> Path:
    from roborsi.embodied.paths import evals_root
    return evals_root()


def run(
    task: str,
    executor: str = "expert",
    backend: str = "robotwin",
    seeds: int = 20,
    seed_start: int = 1000,
    checkpoint: str | None = None,
    max_steps: int = 400,
    action_type: str = "qpos",
    **_: Any,
) -> dict[str, Any]:
    if not task:
        raise ValueError("success_rate requires 'task'")
    if executor == "expert":
        per_seed = _evaluate_expert(task, backend, seeds, seed_start)
    elif executor == "pi0_checkpoint":
        if not checkpoint:
            raise ValueError("pi0_checkpoint executor requires 'checkpoint'")
        per_seed = _evaluate_pi0(
            task, backend, seeds, seed_start,
            checkpoint=checkpoint,
            max_steps=max_steps,
            action_type=action_type,
        )
    elif executor == "rollout_vlm":
        raise NotImplementedError("rollout_vlm evaluation awaits SimActionToolGroup.")
    else:
        raise ValueError(f"unknown executor '{executor}'")

    successes = sum(1 for r in per_seed if r["success"])
    report = {
        "task": task,
        "backend": backend,
        "executor": executor,
        "checkpoint": checkpoint,
        "seeds": len(per_seed),
        "successes": successes,
        "success_rate": successes / len(per_seed) if per_seed else 0.0,
        "per_seed": per_seed,
    }
    out_dir = _default_eval_root() / task / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["report_path"] = str(out_dir / "eval_report.json")
    return report


def _evaluate_expert(task: str, backend: str, seeds: int, seed_start: int) -> list[dict[str, Any]]:
    be = _assert_backend(backend)
    out: list[dict[str, Any]] = []
    with be.make_env(task) as env:
        for i in range(seeds):
            seed = seed_start + i
            rollout = env.run_expert(seed=seed)
            out.append({"seed": seed, "success": rollout.success, "outcome": rollout.outcome})
    return out


def _evaluate_pi0(
    task: str,
    backend: str,
    seeds: int,
    seed_start: int,
    checkpoint: str,
    max_steps: int,
    action_type: str,
) -> list[dict[str, Any]]:
    policy = _load_pi0_policy(checkpoint)
    be = _assert_backend(backend)
    out: list[dict[str, Any]] = []
    with be.make_env(task) as env:
        for i in range(seeds):
            seed = seed_start + i
            out.append(_rollout_policy(env, policy, seed, max_steps, action_type))
    return out


def _rollout_policy(
    env: Env, policy, seed: int, max_steps: int, action_type: str,
) -> dict[str, Any]:
    obs = env.reset(seed)
    done = False
    steps = 0
    while not done and steps < max_steps:
        action = _policy_forward(policy, obs, action_type)
        step = env.step(action, action_type=action_type)
        obs = step.obs
        done = step.done
        steps += 1
    checker = getattr(env, "_impl", None)
    success = False
    if checker is not None:
        predicate = getattr(checker, "check_success", None) or getattr(checker, "_check_success", None)
        if predicate is not None:
            success = bool(predicate())
    return {
        "seed": seed,
        "success": bool(success or done),
        "outcome": "success" if success else ("done" if done else "timeout"),
        "steps": steps,
    }


def _policy_forward(policy, obs: Observation, action_type: str):
    """Translate a Observation into the policy's expected input shape,
    call its ``select_action``, return a flat numpy array.
    """
    import numpy as np
    import torch
    device = next(policy.parameters()).device
    batch: dict[str, Any] = {}
    for cam, img in obs.images.items():
        if img is None:
            continue
        # LeRobot policies expect images as (1, C, H, W) float in [0, 1].
        arr = np.asarray(img).astype("float32") / 255.0
        if arr.ndim == 3:
            arr = np.transpose(arr, (2, 0, 1))
        batch[f"observation.images.{cam}"] = torch.from_numpy(arr).unsqueeze(0).to(device)
    if obs.state is not None:
        state = _flatten_state(obs.state)
        batch["observation.state"] = torch.from_numpy(state).unsqueeze(0).to(device)
    out = policy.select_action(batch)
    if isinstance(out, torch.Tensor):
        arr = out.detach().cpu().numpy().squeeze()
    else:
        arr = np.asarray(out).squeeze()
    return arr


def _flatten_state(state: Any) -> "np.ndarray":  # noqa: F821
    import numpy as np
    if isinstance(state, dict):
        parts = []
        for key in ("left_arm", "left_gripper", "right_arm", "right_gripper"):
            if key in state:
                parts.append(np.asarray(state[key], dtype="float32").flatten())
        if parts:
            return np.concatenate(parts)
    if hasattr(state, "tolist"):
        return np.asarray(state, dtype="float32").flatten()
    return np.asarray(state, dtype="float32").flatten()


def _assert_backend(backend: str):
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"backend '{backend}' unavailable: {reason}")
    return be


def _load_pi0_policy(checkpoint: str):
    """Load a LeRobot-trained policy (pi0/ACT/DP/…) from a local checkpoint.

    LeRobot's ``make_policy`` needs ``ds_meta`` (dataset metadata) to bind
    feature shapes. We look for a sibling ``train_config.json`` left by
    lerobot-train; it records the dataset path used at training time.
    """
    from pathlib import Path
    from lerobot.policies.factory import make_policy
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDatasetMetadata
    cfg = PreTrainedConfig.from_pretrained(checkpoint)

    train_cfg_path = Path(checkpoint) / "train_config.json"
    ds_meta = None
    if train_cfg_path.exists():
        import json
        train_cfg = json.loads(train_cfg_path.read_text(encoding="utf-8"))
        ds_block = train_cfg.get("dataset", {})
        repo_id = ds_block.get("repo_id")
        ds_root = ds_block.get("root")
        if repo_id and ds_root:
            ds_meta = LeRobotDatasetMetadata(repo_id=repo_id, root=ds_root)

    if ds_meta is None:
        raise RuntimeError(
            f"Cannot load policy from {checkpoint}: no dataset metadata sidecar."
        )
    policy = make_policy(cfg=cfg, ds_meta=ds_meta)
    policy.eval()
    return policy
