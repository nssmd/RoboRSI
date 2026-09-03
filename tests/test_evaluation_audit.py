from __future__ import annotations

import json
from pathlib import Path

from roborsi.evaluation.audit import audit_libero_short_suite


def _write_campaign(root: Path) -> None:
    (root / "campaign.json").write_text(
        json.dumps(
            {
                "schema": "roborsi.libero_short_campaign.v1",
                "campaign_id": "test-campaign",
                "backend": "libero-pro",
                "task_keys": ["libero_goal_lan/0", "libero_object_swap/1"],
                "pass_at": 2,
                "seed_start": 0,
                "workers": 2,
                "tool_budget": 80,
                "models": {
                    "planner": "test",
                    "engineer": "test",
                    "reviewer": "test",
                },
                "runtime": {"roborsi_commit": "test", "roborsi_dirty": False},
            }
        )
    )


def _row(task: str, seed: int, verdict: str) -> dict:
    success = True if verdict == "success" else False if verdict == "failure" else None
    return {
        "task_key": task,
        "sim_task": task,
        "backend": "libero-pro",
        "seed": seed,
        "run_mode": "eval",
        "verdict": verdict,
        "status": "terminal" if verdict in {"success", "failure"} else "incomplete",
        "success": success,
        "outcome": verdict,
    }


def _write_rows(root: Path, rows: list[dict]) -> None:
    (root / "episodes.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_audit_recomputes_complete_task_pass_at_two(tmp_path: Path) -> None:
    _write_campaign(tmp_path)
    _write_rows(
        tmp_path,
        [
            _row("libero_goal_lan/0", 0, "success"),
            _row("libero_object_swap/1", 0, "failure"),
            _row("libero_object_swap/1", 1, "success"),
        ],
    )

    report = audit_libero_short_suite(tmp_path)

    assert report["integrity_status"] == "pass"
    assert report["campaign_status"] == "complete"
    assert report["recomputed"]["tasks_solved"] == 2
    assert report["recomputed"]["episode_successes"] == 2
    assert report["recomputed"]["episode_failures"] == 1


def test_audit_excludes_infrastructure_and_marks_incomplete(tmp_path: Path) -> None:
    _write_campaign(tmp_path)
    _write_rows(
        tmp_path,
        [
            _row("libero_goal_lan/0", 0, "infra"),
            _row("libero_object_swap/1", 0, "failure"),
        ],
    )

    report = audit_libero_short_suite(tmp_path)

    assert report["integrity_status"] == "pass"
    assert report["campaign_status"] == "incomplete"
    assert report["recomputed"]["infra_count"] == 1
    assert report["recomputed"]["episode_failures"] == 1


def test_audit_rejects_conflicting_terminal_verdicts(tmp_path: Path) -> None:
    _write_campaign(tmp_path)
    _write_rows(
        tmp_path,
        [
            _row("libero_goal_lan/0", 0, "failure"),
            _row("libero_goal_lan/0", 0, "success"),
        ],
    )

    report = audit_libero_short_suite(tmp_path)

    assert report["integrity_status"] == "fail"
    assert any("conflicting terminal verdicts" in error for error in report["errors"])
    assert any("duplicate terminal verdicts" in error for error in report["errors"])


def test_audit_detects_success_lock_violation(tmp_path: Path) -> None:
    _write_campaign(tmp_path)
    _write_rows(
        tmp_path,
        [
            _row("libero_goal_lan/0", 0, "success"),
            _row("libero_goal_lan/0", 1, "failure"),
        ],
    )

    report = audit_libero_short_suite(tmp_path)

    assert report["integrity_status"] == "fail"
    assert any("success-lock violation" in error for error in report["errors"])
