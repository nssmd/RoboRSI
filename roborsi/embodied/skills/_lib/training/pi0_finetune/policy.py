"""training.pi0_finetune — subprocess wrapper around ``lerobot-train``.

Design:
- Sync subprocess (policies run in-process; async buys nothing here).
- Stream stdout → current console so long runs are debuggable.
- Return checkpoint path + last log line on success.
- Let ``lerobot-train`` do its own resume/HF-pull/scheduler logic — we
  just hand it CLI args.

Env inheritance: we forward ``HF_ENDPOINT`` / ``HF_TOKEN`` / ``HTTPS_PROXY``
from roborsi's runtime config (same mechanism as ``SubprocessExecutor``
in roborsi/embodied/executor.py — reuse that helper).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _default_output_root() -> Path:
    from roborsi.embodied.paths import checkpoints_root
    return checkpoints_root()


def _policy_type(base_model: str) -> str:
    m = {"pi0": "pi0", "pi0.5": "pi0", "pi0-fast": "pi0fast"}
    if base_model not in m:
        raise ValueError(f"unknown base_model '{base_model}'")
    return m[base_model]


def _policy_path(base_model: str) -> str:
    return {
        "pi0": "lerobot/pi0",
        "pi0.5": "lerobot/pi05_base",
        "pi0-fast": "lerobot/pi0fast_base",
        "act": "lerobot/act",
        "dp": "lerobot/diffusion",
    }[base_model]


def _training_env(offline: bool = True) -> dict[str, str]:
    """Inherit current env, overlay HF endpoint/token/proxy from config."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if offline:
        # We're loading a local dataset from disk; do not let HF Hub interrupt.
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
    try:
        from roborsi.config.loader import load_runtime_config
        hf = load_runtime_config().huggingface
        if hf.endpoint:
            env.setdefault("HF_ENDPOINT", hf.endpoint)
        if hf.token:
            env.setdefault("HF_TOKEN", hf.token)
        if hf.proxy:
            env.setdefault("HTTPS_PROXY", hf.proxy)
            env.setdefault("HTTP_PROXY", hf.proxy)
    except Exception:
        pass
    return env


def run(
    dataset: str,
    base_model: str = "pi0.5",
    steps: int = 20000,
    batch_size: int = 8,
    lr: float = 2.5e-5,
    output_dir: str | None = None,
    device: str = "cuda",
    wandb: bool = False,
    dry_run: bool = False,
    **_: Any,
) -> dict[str, Any]:
    if not dataset:
        raise ValueError("pi0_finetune requires dataset")
    if output_dir:
        out = Path(output_dir).expanduser()
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _default_output_root() / dataset / f"{base_model}-{ts}"
    out.parent.mkdir(parents=True, exist_ok=True)   # do NOT mkdir 'out' itself; lerobot-train wants it absent

    # Resolve dataset root: ~/.roborsi/datasets/<simple_name>/
    # If caller passed 'namespace/name' repo_id, map to local datasets dir using just <name>.
    from roborsi.embodied.paths import datasets_root
    simple_name = dataset.split("/")[-1]
    dataset_root = (datasets_root() / simple_name).resolve()
    if not dataset_root.is_dir():
        raise RuntimeError(f"dataset '{simple_name}' not found at {dataset_root}")

    binary = shutil.which("lerobot-train")
    if binary is None:
        raise RuntimeError(
            "'lerobot-train' not on PATH. Install LeRobot in the active "
            "python env (pip install lerobot[pi] or pip install -e "
            "<RoboRSI>/roborsi/embodied/engine)."
        )

    # ACT / DP have no published pretrained checkpoints — instantiate fresh
    # via --policy.type. pi0 family ships ckpts → use --policy.path.
    fresh_init_models = {"act", "dp", "diffusion"}
    if base_model in fresh_init_models:
        policy_arg = f"--policy.type={base_model if base_model != 'dp' else 'diffusion'}"
    else:
        policy_arg = f"--policy.path={_policy_path(base_model)}"

    cmd = [
        binary,
        f"--dataset.repo_id={dataset}",
        f"--dataset.root={dataset_root}",
        policy_arg,
        f"--policy.repo_id=local/{simple_name}-{base_model}",
        f"--policy.push_to_hub=false",
        f"--steps={steps}",
        f"--batch_size={batch_size}",
        f"--optimizer.lr={lr}",
        f"--output_dir={out}",
        f"--policy.device={device}",
    ]
    if wandb:
        cmd.append("--wandb.enable=true")

    if dry_run:
        return {
            "skill": "pi0_finetune",
            "dry_run": True,
            "command": " ".join(shlex.quote(c) for c in cmd),
            "output_dir": str(out),
        }

    # Stream output to parent stdout — operator needs to see training progress.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_training_env(),
        text=True,
        bufsize=1,
    )
    last_line = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if line.strip():
            last_line = line.rstrip()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"lerobot-train exited {rc}. Last line: {last_line}")

    return {
        "skill": "pi0_finetune",
        "dataset": dataset,
        "base_model": base_model,
        "output_dir": str(out),
        "last_log": last_line,
    }
