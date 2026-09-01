from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi.embodied.sim.libero.run_records import (
    EpisodeIdentity,
    EpisodeRecord,
    append_record,
)
from roborsi.libero.config import ReleaseConfig
from roborsi.libero.launcher import create_campaign
from roborsi.libero.supervisor import refresh_campaign_state, summarize_campaign

pytestmark = pytest.mark.runtime


def _append(campaign: Path, task: str, seed: int, category: str) -> None:
    success = {"task_success": True, "task_failure": False, "implementation_failure": False}.get(
        category
    )
    append_record(
        campaign / "journals/seed-0-worker-0.episodes.jsonl",
        EpisodeRecord(
            identity=EpisodeIdentity("run", task, seed, 0, 1),
            category=category,
            success=success,
            outcome=category if success is not None else None,
            total_tokens=10,
            vlm_calls=1,
            elapsed_s=2.0,
            code_fingerprint="release:r1",
        ),
    )


def test_refresh_ingests_terminal_rows_and_keeps_infrastructure_retryable(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run", release_id="r1")
    _append(campaign, "libero_spatial/0", 0, "task_success")
    _append(campaign, "libero_spatial/1", 0, "task_failure")
    _append(campaign, "libero_goal/0", 0, "provider_failure")

    state = refresh_campaign_state(campaign)

    assert state.solved_tasks == {"libero_spatial/0"}
    assert ("libero_spatial/1", 0) in state.terminal_pairs
    assert ("libero_goal/0", 0) not in state.terminal_pairs
    assert state.infrastructure_excluded == 1
    persisted = json.loads((campaign / "state.json").read_text(encoding="utf-8"))
    assert persisted["solved_tasks"] == ["libero_spatial/0"]


def test_summary_uses_task_level_coverage_and_full_fixed_denominator(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run", release_id="r1")
    _append(campaign, "libero_object/0", 0, "task_success")
    _append(campaign, "libero_object/1", 0, "task_failure")

    summary = summarize_campaign(campaign)

    assert summary["solved_tasks"] == 1
    assert summary["total_tasks"] == 120
    assert summary["rate"] == 1 / 120
    assert summary["by_suite"]["libero_object"]["solved_tasks"] == 1
    assert summary["verdicts"] == {
        "task_success": 1,
        "task_failure": 1,
        "implementation_failure": 0,
        "infrastructure_excluded": 0,
    }
