"""Portable campaign creation and worker command construction."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from roborsi.libero.catalog import SHORT_TASK_CATALOG
from roborsi.libero.config import ReleaseConfig
from roborsi.libero.protocol import CampaignState

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class WorkerCommand:
    worker: int
    task_keys: tuple[str, ...]
    argv: tuple[str, ...]
    log_path: Path
    env: dict[str, str]


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_campaign(
    config: ReleaseConfig,
    *,
    mode: Literal["adaptive", "fixed"],
    run_id: str,
    release_id: str = "roborsi-0.1.0",
) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a simple filesystem-safe identifier")
    root = config.runtime.results_root / run_id
    root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": "roborsi.libero_short_campaign.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "mode": mode,
        "metric": (
            "task_level_adaptive_pass_at_10"
            if mode == "adaptive"
            else "task_level_fixed_pass_at_10"
        ),
        "claim_scope": (
            "adaptive_cross_release_campaign"
            if mode == "adaptive"
            else "single_release_fixed_evaluation"
        ),
        "task_count": len(SHORT_TASK_CATALOG),
        "task_catalog": list(SHORT_TASK_CATALOG),
        "seeds": list(config.evaluation.seeds),
        "model": config.provider.model,
        "reasoning_effort": config.provider.reasoning_effort,
        "controller": config.simulator.controller,
        "image_size": config.simulator.image_size,
        "horizon": config.simulator.horizon,
        "tool_budget": config.evaluation.tool_budget,
        "release_id": release_id,
        "success_source": config.integrity.success_source,
        "selfevo_frozen": mode == "fixed",
        "retain_all_artifacts": True,
    }
    _write_new(root / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _write_new(
        root / "config.resolved.yaml",
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
    )
    state = CampaignState.new(mode=mode, release_id=release_id)
    _write_new(root / "state.json", json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")
    (root / "journals").mkdir()
    (root / "logs").mkdir()
    (root / "media").mkdir()
    (root / "traces").mkdir()
    (root / "trajectories").mkdir()
    (root / "proposals").mkdir()
    return root


def _worker_count(config: ReleaseConfig, task_count: int) -> int:
    configured = int(config.runtime.workers)
    if configured > 0:
        return min(configured, task_count)
    available = max(1, os.cpu_count() or 1)
    return min(32, available, task_count)


def build_worker_commands(
    config: ReleaseConfig,
    *,
    campaign_root: Path,
    task_keys: list[str],
    seed: int,
    release_id: str,
) -> list[WorkerCommand]:
    if len(task_keys) != len(set(task_keys)):
        raise ValueError("task_keys contain duplicates")
    unknown = sorted(set(task_keys) - set(SHORT_TASK_CATALOG))
    if unknown:
        raise ValueError(f"unknown LIBERO short tasks: {unknown}")
    if not task_keys:
        return []
    workers = _worker_count(config, len(task_keys))
    python = config.runtime.python or sys.executable
    commands: list[WorkerCommand] = []
    for worker in range(workers):
        assigned = tuple(task_keys[worker::workers])
        if not assigned:
            continue
        argv = (
            python,
            "-m",
            "roborsi.libero.worker",
            "--config",
            str(campaign_root / "config.resolved.yaml"),
            "--campaign",
            str(campaign_root),
            "--seed",
            str(seed),
            "--release-id",
            release_id,
            "--worker",
            str(worker),
            "--workers",
            str(workers),
            "--task-keys",
            *assigned,
        )
        commands.append(
            WorkerCommand(
                worker=worker,
                task_keys=assigned,
                argv=argv,
                log_path=campaign_root / "logs" / f"seed-{seed}-worker-{worker}.log",
                env=(
                    {
                        "CUDA_VISIBLE_DEVICES": str(
                            config.runtime.gpu_devices[
                                worker % len(config.runtime.gpu_devices)
                            ]
                        )
                    }
                    if config.runtime.gpu_devices
                    else {}
                ),
            )
        )
    return commands


def launch_evaluation(
    config: ReleaseConfig,
    *,
    mode: Literal["adaptive", "fixed"],
) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{mode}-short"
    campaign = create_campaign(config, mode=mode, run_id=run_id)
    command = [
        config.runtime.python or sys.executable,
        "-m",
        "roborsi.libero.supervisor",
        "--campaign",
        str(campaign),
    ]
    env = os.environ.copy()
    env.update(config.runtime_environment(env))
    log = (campaign / "supervisor.log").open("ab")
    subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return campaign
