from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi_libero.dashboard import (
    load_dashboard_payload,
    render_dashboard_html,
    write_dashboard_html,
)


def _payload() -> dict:
    return {
        "schema": "roborsi.libero_short_replay.v1",
        "metric": "task_level_adaptive_pass_at_k",
        "claim_scope": "adaptive_cross_release_campaign",
        "k": 3,
        "solved_tasks": 3,
        "total_tasks": 4,
        "rate": 0.75,
        "pass_curve": [1, 2, 3],
        "by_suite": {
            "libero_goal": {"solved_tasks": 1, "total_tasks": 1, "rate": 1.0},
            "libero_90": {"solved_tasks": 2, "total_tasks": 3, "rate": 2 / 3},
        },
        "task_success_records": 3,
        "task_failure_records": 2,
        "implementation_failures": 1,
        "infrastructure_excluded": 4,
        "total_tokens": 123,
        "median_total_tokens": 41.0,
        "total_vlm_calls": 9,
        "total_elapsed_s": 12.5,
    }


def test_render_dashboard_html_contains_result_and_scope() -> None:
    text = render_dashboard_html(_payload())

    assert "3 / 4" in text
    assert "75.0%" in text
    assert "Adaptive cross-release coverage" in text
    assert "LIBERO-90" in text


def test_write_dashboard_html_from_json(tmp_path: Path) -> None:
    source = tmp_path / "result.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    output = tmp_path / "dashboard.html"

    destination = write_dashboard_html(output, result_path=source)

    assert destination == output.resolve()
    assert "RoboRSI Evidence Console" in output.read_text(encoding="utf-8")


def test_dashboard_loads_campaign_directory(tmp_path: Path) -> None:
    campaign = tmp_path / "runs/run-a"
    campaign.mkdir(parents=True)
    (campaign / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "roborsi.libero_short_campaign.v1",
                "created_at": "2026-08-31T10:00:00+00:00",
                "run_id": "run-a",
                "mode": "adaptive",
                "metric": "task_level_adaptive_pass_at_10",
                "claim_scope": "adaptive_cross_release_campaign",
                "task_count": 120,
                "task_catalog": ["libero_spatial/0"],
                "seeds": list(range(10)),
                "model": "responses/gpt-5.6-sol",
                "reasoning_effort": "medium",
                "controller": "JOINT_POSITION",
                "image_size": 512,
                "horizon": 5000,
                "release_id": "r1",
            }
        ),
        encoding="utf-8",
    )
    (campaign / "state.json").write_text(
        json.dumps(
            {
                "release_history": ["r1"],
                "current_release_id": "r1",
                "completed_seeds": [],
                "status": "running",
                "solved_tasks": [],
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    payload = load_dashboard_payload(campaign_root=campaign)
    text = render_dashboard_html(payload)

    assert payload["run_id"] == "run-a"
    assert "RUN-A" in text
    assert "Campaign status" in text


def test_dashboard_rejects_multiple_sources(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(_payload()), encoding="utf-8")

    with pytest.raises(ValueError, match="one dashboard source"):
        load_dashboard_payload(result_path=result, campaign_root=tmp_path)


def test_running_campaign_refresh_is_server_only() -> None:
    payload = _payload()
    payload.update({"source_kind": "campaign", "status": "running", "run_id": "run-a"})

    static = render_dashboard_html(payload)
    served = render_dashboard_html(payload, auto_refresh=True)

    assert 'http-equiv="refresh"' not in static
    assert 'http-equiv="refresh"' in served
