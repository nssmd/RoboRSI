from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi_libero.runs import (
    discover_campaigns,
    load_campaign_payload,
    resolve_campaign,
)


def _write_campaign(
    results_root: Path,
    run_id: str,
    *,
    created_at: str,
    with_result: bool,
) -> Path:
    campaign = results_root / run_id
    campaign.mkdir(parents=True)
    manifest = {
        "schema": "roborsi.libero_short_campaign.v1",
        "created_at": created_at,
        "run_id": run_id,
        "mode": "adaptive",
        "metric": "task_level_adaptive_pass_at_10",
        "claim_scope": "adaptive_cross_release_campaign",
        "task_count": 120,
        "task_catalog": ["libero_spatial/0", "libero_goal/0"],
        "seeds": list(range(10)),
        "model": "responses/gpt-5.6-sol",
        "reasoning_effort": "medium",
        "controller": "JOINT_POSITION",
        "image_size": 512,
        "horizon": 5000,
        "release_id": "r1",
    }
    state = {
        "schema": "roborsi.libero_short_campaign_state.v1",
        "mode": "adaptive",
        "task_catalog": ["libero_spatial/0", "libero_goal/0"],
        "seeds": list(range(10)),
        "release_history": ["r1", "r2"],
        "current_release_id": "r2",
        "selfevo_frozen": False,
        "records": [
            {
                "task_key": "libero_spatial/0",
                "seed": 0,
                "category": "task_success",
                "simulator_verdict": "task_success",
                "release_id": "r1",
                "attempt": 1,
            },
            {
                "task_key": "libero_goal/0",
                "seed": 0,
                "category": "task_failure",
                "simulator_verdict": "task_failure",
                "release_id": "r1",
                "attempt": 1,
            },
            {
                "task_key": "libero_goal/0",
                "seed": 1,
                "category": "provider_failure",
                "simulator_verdict": None,
                "release_id": "r2",
                "attempt": 1,
            },
        ],
        "completed_seeds": [0],
        "status": "running",
        "solved_tasks": ["libero_spatial/0"],
        "infrastructure_excluded": 1,
    }
    (campaign / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (campaign / "state.json").write_text(json.dumps(state), encoding="utf-8")
    journals = campaign / "journals"
    journals.mkdir()
    if with_result:
        result = {
            "schema": "roborsi.libero_short_campaign_summary.v1",
            "run_id": run_id,
            "mode": "adaptive",
            "metric": "task_level_adaptive_pass_at_10",
            "claim_scope": "adaptive_cross_release_campaign",
            "status": "complete",
            "completed_seeds": list(range(10)),
            "solved_tasks": 2,
            "solved_task_keys": ["libero_goal/0", "libero_spatial/0"],
            "total_tasks": 120,
            "rate": 2 / 120,
            "pass_curve": [1, 2, 2, 2, 2, 2, 2, 2, 2, 2],
            "by_suite": {
                "libero_spatial": {
                    "solved_tasks": 1,
                    "total_tasks": 10,
                    "rate": 0.1,
                },
                "libero_goal": {
                    "solved_tasks": 1,
                    "total_tasks": 10,
                    "rate": 0.1,
                },
            },
            "release_history": ["r1", "r2"],
            "verdicts": {
                "task_success": 2,
                "task_failure": 3,
                "implementation_failure": 1,
                "infrastructure_excluded": 4,
            },
            "efficiency": {
                "total_tokens": 1000,
                "median_total_tokens": 125.0,
                "total_vlm_calls": 20,
                "total_episode_elapsed_s": 3600.0,
            },
        }
        (campaign / "result.json").write_text(json.dumps(result), encoding="utf-8")
    else:
        journal_rows = [
            {
                "identity": {
                    "run_id": run_id,
                    "task_key": "libero_spatial/0",
                    "seed": 0,
                    "shard": 0,
                    "attempt": 1,
                },
                "category": "task_success",
                "total_tokens": 100,
                "vlm_calls": 2,
                "elapsed_s": 10.0,
            },
            {
                "identity": {
                    "run_id": run_id,
                    "task_key": "libero_goal/0",
                    "seed": 0,
                    "shard": 0,
                    "attempt": 1,
                },
                "category": "task_failure",
                "total_tokens": 300,
                "vlm_calls": 4,
                "elapsed_s": 20.0,
            },
            {
                "identity": {
                    "run_id": run_id,
                    "task_key": "libero_goal/0",
                    "seed": 1,
                    "shard": 0,
                    "attempt": 1,
                },
                "category": "provider_failure",
                "total_tokens": 0,
                "vlm_calls": 0,
                "elapsed_s": 1.0,
            },
        ]
        (journals / "seed-0-worker-0.episodes.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in journal_rows),
            encoding="utf-8",
        )
    return campaign


def test_discover_campaigns_returns_newest_first(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _write_campaign(root, "older", created_at="2026-08-30T10:00:00+00:00", with_result=False)
    _write_campaign(root, "newer", created_at="2026-08-31T10:00:00+00:00", with_result=True)

    campaigns = discover_campaigns(root)

    assert [campaign.run_id for campaign in campaigns] == ["newer", "older"]
    assert campaigns[0].status == "complete"
    assert campaigns[1].solved_tasks == 1


def test_resolve_campaign_accepts_id_and_path(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    campaign = _write_campaign(
        root,
        "run-a",
        created_at="2026-08-31T10:00:00+00:00",
        with_result=False,
    )

    assert resolve_campaign(root, "run-a") == campaign.resolve()
    assert resolve_campaign(root, campaign) == campaign.resolve()

    with pytest.raises(FileNotFoundError, match="campaign"):
        resolve_campaign(root, "missing")


def test_load_running_campaign_normalizes_dashboard_fields(tmp_path: Path) -> None:
    campaign = _write_campaign(
        tmp_path / "runs",
        "run-a",
        created_at="2026-08-31T10:00:00+00:00",
        with_result=False,
    )

    payload = load_campaign_payload(campaign)

    assert payload["source_kind"] == "campaign"
    assert payload["run_id"] == "run-a"
    assert payload["status"] == "running"
    assert payload["k"] == 10
    assert payload["completed_passes"] == 1
    assert payload["solved_tasks"] == 1
    assert payload["pass_curve"][:2] == [1, 1]
    assert payload["verdicts"]["task_failure"] == 1
    assert payload["infrastructure_excluded"] == 1
    assert payload["total_tokens"] == 400
    assert payload["median_total_tokens"] == 200.0
    assert payload["total_vlm_calls"] == 6
    assert payload["total_elapsed_s"] == 30.0


def test_load_completed_campaign_flattens_efficiency(tmp_path: Path) -> None:
    campaign = _write_campaign(
        tmp_path / "runs",
        "run-a",
        created_at="2026-08-31T10:00:00+00:00",
        with_result=True,
    )

    payload = load_campaign_payload(campaign)

    assert payload["status"] == "complete"
    assert payload["median_total_tokens"] == 125.0
    assert payload["total_elapsed_s"] == 3600.0
    assert payload["total_vlm_calls"] == 20
