from __future__ import annotations

from pathlib import Path

import pytest

from roborsi.embodied.sim.libero.run_records import load_records
from roborsi.libero.config import ReleaseConfig
from roborsi.libero.launcher import create_campaign
from roborsi.libero.worker import run_assigned_tasks, skill_for_task

pytestmark = pytest.mark.runtime


def test_worker_routes_only_known_direct_manipulation_tasks() -> None:
    assert skill_for_task("libero_goal/5") == "libero_direct_manipulation"
    assert skill_for_task("libero_90/35") == "libero_direct_manipulation"
    assert skill_for_task("libero_90/31") == "libero_pick_place"
    assert skill_for_task("libero_object/4") == "libero_pick_place"


def test_worker_rejects_tasks_outside_short_catalog() -> None:
    with pytest.raises(ValueError, match="short catalog"):
        skill_for_task("libero_10/0")


def test_worker_records_native_success_and_skips_it_on_resume(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    calls: list[str] = []

    def run_episode(**kwargs):
        calls.append(kwargs["task"])
        return {
            "success": True,
            "outcome": "predicate_passed_without_done",
            "steps": 3,
            "meta": {
                "total_tokens": 12,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "vlm_calls": 1,
                "rollout_video": "success.mp4",
                "trajectory_path": "episode.parquet",
            },
        }

    journal = run_assigned_tasks(
        config,
        campaign_root=campaign,
        seed=0,
        release_id="release-public",
        worker=0,
        task_keys=["libero_spatial/0"],
        run_episode=run_episode,
    )
    run_assigned_tasks(
        config,
        campaign_root=campaign,
        seed=0,
        release_id="release-public",
        worker=0,
        task_keys=["libero_spatial/0"],
        run_episode=run_episode,
    )

    rows = load_records(journal)
    assert calls == ["libero_spatial/0"]
    assert len(rows) == 1
    assert rows[0].category == "task_success"
    assert rows[0].success is True
    assert rows[0].total_tokens == 12


def test_worker_retains_provider_failure_without_consuming_task_seed(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    attempts = 0

    class ProviderDownError(RuntimeError):
        category = "provider_failure"
        detail = "provider unavailable"
        usage = {"total_tokens": 0}

    def fail_episode(**kwargs):
        nonlocal attempts
        attempts += 1
        raise ProviderDownError()

    journal = run_assigned_tasks(
        config,
        campaign_root=campaign,
        seed=0,
        release_id="release-public",
        worker=0,
        task_keys=["libero_goal/0", "libero_goal/1"],
        run_episode=fail_episode,
    )
    run_assigned_tasks(
        config,
        campaign_root=campaign,
        seed=0,
        release_id="release-public",
        worker=0,
        task_keys=["libero_goal/0"],
        run_episode=fail_episode,
    )

    rows = load_records(journal)
    assert attempts == 2
    assert [row.category for row in rows] == ["provider_failure", "provider_failure"]
    assert [row.identity.attempt for row in rows] == [1, 2]
