from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from roborsi_libero.catalog import SHORT_TASK_CATALOG
from roborsi_libero.config import ReleaseConfig
from roborsi_libero.launcher import build_worker_commands, create_campaign


def test_campaign_manifest_is_secret_free_and_create_once(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)

    campaign = create_campaign(config, mode="adaptive", run_id="test-run")
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == "roborsi.libero_short_campaign.v1"
    assert manifest["task_count"] == 120
    assert manifest["seeds"] == list(range(10))
    assert manifest["mode"] == "adaptive"
    assert "api_key" not in json.dumps(manifest).lower()
    with pytest.raises(FileExistsError):
        create_campaign(config, mode="adaptive", run_id="test-run")


def test_worker_commands_cover_each_task_once_without_private_paths(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path).model_copy(
        update={
            "runtime": ReleaseConfig.default(repo_root=tmp_path).runtime.model_copy(
                update={"workers": 7, "python": "/usr/bin/python3"}
            )
        }
    )
    campaign = create_campaign(config, mode="adaptive", run_id="test-run")

    commands = build_worker_commands(
        config,
        campaign_root=campaign,
        task_keys=list(SHORT_TASK_CATALOG),
        seed=0,
        release_id="release-public",
    )

    assert len(commands) == 7
    assigned = [task for command in commands for task in command.task_keys]
    assert len(assigned) == 120
    assert len(set(assigned)) == 120
    for command in commands:
        rendered = " ".join(command.argv)
        assert "/mnt" + "/workspace" not in rendered
        assert "/data" + "/yijia" not in rendered
        assert "--seed 0" in rendered
        assert "--release-id release-public" in rendered


def test_worker_uses_current_environment_python_by_default(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="test-run")

    commands = build_worker_commands(
        config,
        campaign_root=campaign,
        task_keys=["libero_goal/0"],
        seed=0,
        release_id="release-public",
    )

    assert commands[0].argv[0] == sys.executable


def test_worker_commands_round_robin_configured_gpus(tmp_path: Path) -> None:
    base = ReleaseConfig.default(repo_root=tmp_path)
    config = base.model_copy(
        update={
            "runtime": base.runtime.model_copy(
                update={"workers": 4, "gpu_devices": [2, 5]}
            )
        }
    )
    campaign = create_campaign(config, mode="adaptive", run_id="test-run")

    commands = build_worker_commands(
        config,
        campaign_root=campaign,
        task_keys=list(SHORT_TASK_CATALOG[:8]),
        seed=0,
        release_id="release-public",
    )

    assert [command.env["CUDA_VISIBLE_DEVICES"] for command in commands] == [
        "2",
        "5",
        "2",
        "5",
    ]
