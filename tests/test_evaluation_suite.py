from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi.evaluation import suite


def _row(task_key: str, seed: int, verdict: str) -> dict:
    success = True if verdict == "success" else False if verdict == "failure" else None
    return {
        "task": "libero_pick_place",
        "task_key": task_key,
        "sim_task": task_key,
        "backend": "libero-pro",
        "seed": seed,
        "run_mode": "eval",
        "success": success,
        "verdict": verdict,
        "status": "terminal" if success is not None else "incomplete",
        "outcome": verdict,
        "tool_calls": 2,
    }


def _stable_runtime(_backend: str) -> dict:
    return {"roborsi_commit": "test", "backend": "libero-pro"}


def test_suite_resume_preserves_successes_and_exact_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tasks = ["libero_spatial/0", "libero_object_task/1"]
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: tasks,
    )
    monkeypatch.setattr(suite, "_runtime_fingerprint", _stable_runtime)

    def fake_attempt(payload: dict) -> list[dict]:
        key = (payload["task_key"], payload["seed"])
        calls.append(key)
        verdict = (
            "success"
            if key in {("libero_spatial/0", 0), ("libero_object_task/1", 1)}
            else "failure"
        )
        row = _row(*key, verdict)
        row["attempt"] = payload["attempt_start"]
        return [row]

    monkeypatch.setattr(suite, "_run_suite_attempt", fake_attempt)
    first = suite.run_libero_short_suite(
        seeds=2,
        workers=1,
        out_dir=tmp_path,
    )

    assert first["status"] == "complete"
    assert first["tasks_solved"] == 2
    assert calls == [
        ("libero_spatial/0", 0),
        ("libero_object_task/1", 0),
        ("libero_object_task/1", 1),
    ]
    campaign_id = first["campaign_id"]

    calls.clear()
    resumed = suite.run_libero_short_suite(
        seeds=2,
        workers=1,
        out_dir=tmp_path,
    )

    assert resumed["campaign_id"] == campaign_id
    assert resumed["tasks_solved"] == 2
    assert calls == []
    assert len((tmp_path / "episodes.jsonl").read_text().splitlines()) == 3


def test_suite_refuses_incompatible_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: ["libero_goal/0"],
    )
    monkeypatch.setattr(suite, "_runtime_fingerprint", _stable_runtime)
    monkeypatch.setattr(
        suite,
        "_run_suite_attempt",
        lambda payload: [
            {
                **_row(payload["task_key"], payload["seed"], "success"),
                "attempt": payload["attempt_start"],
            }
        ],
    )

    suite.run_libero_short_suite(
        seeds=1,
        workers=1,
        tool_budget=20,
        out_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="tool_budget"):
        suite.run_libero_short_suite(
            seeds=1,
            workers=1,
            tool_budget=21,
            out_dir=tmp_path,
        )


def test_suite_pins_reasoning_effort_in_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: ["libero_goal/0"],
    )
    monkeypatch.setattr(suite, "_runtime_fingerprint", _stable_runtime)
    monkeypatch.setattr(
        suite,
        "_run_suite_attempt",
        lambda payload: [{
            **_row(payload["task_key"], payload["seed"], "success"),
            "attempt": payload["attempt_start"],
        }],
    )

    summary = suite.run_libero_short_suite(
        seeds=1,
        workers=1,
        reasoning_effort="medium",
        out_dir=tmp_path,
    )

    manifest = json.loads((tmp_path / "campaign.json").read_text())
    assert manifest["reasoning_effort"] == "medium"
    assert summary["reasoning_effort"] == "medium"
    with pytest.raises(ValueError, match="reasoning_effort"):
        suite.run_libero_short_suite(
            seeds=1,
            workers=1,
            reasoning_effort="high",
            out_dir=tmp_path,
        )


def test_suite_refuses_dirty_source_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: ["libero_goal/0"],
    )
    monkeypatch.setattr(
        suite,
        "_runtime_fingerprint",
        lambda _backend: {"roborsi_dirty": True},
    )

    with pytest.raises(RuntimeError, match="clean RoboRSI worktree"):
        suite.run_libero_short_suite(
            seeds=1,
            workers=1,
            out_dir=tmp_path,
        )


def test_resolved_implementation_error_does_not_poison_campaign(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: ["libero_goal_lan/0"],
    )
    monkeypatch.setattr(suite, "_runtime_fingerprint", _stable_runtime)
    verdicts = iter(["implementation_error", "success"])

    def fake_attempt(payload: dict) -> list[dict]:
        row = _row(payload["task_key"], payload["seed"], next(verdicts))
        row["attempt"] = payload["attempt_start"]
        return [row]

    monkeypatch.setattr(suite, "_run_suite_attempt", fake_attempt)
    first = suite.run_libero_short_suite(
        seeds=1,
        workers=1,
        out_dir=tmp_path,
    )
    assert first["status"] == "incomplete"
    assert first["unresolved_implementation_error_count"] == 1

    second = suite.run_libero_short_suite(
        seeds=1,
        workers=1,
        out_dir=tmp_path,
    )
    assert second["status"] == "complete"
    assert second["implementation_error_count"] == 1
    assert second["unresolved_implementation_error_count"] == 0


def test_suite_bounds_worker_queue_and_continues_after_interruption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tasks = [
        f"libero_goal_{suite_name}/{index}"
        for suite_name in ("lan", "object")
        for index in range(10)
    ][:17]
    batches: list[list[str]] = []

    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: tasks,
    )
    monkeypatch.setattr(suite, "_runtime_fingerprint", _stable_runtime)

    def fake_batch(
        payloads: list[dict],
        workers: int,
    ) -> list[tuple[dict, list[dict]]]:
        assert len(payloads) <= workers * suite._TASKS_PER_PROCESS
        batches.append([payload["task_key"] for payload in payloads])
        results = []
        for payload in payloads:
            verdict = (
                "implementation_error"
                if payload["task_key"] == tasks[0]
                else "failure"
            )
            row = _row(payload["task_key"], payload["seed"], verdict)
            row["attempt"] = payload["attempt_start"]
            results.append((payload, [row]))
        return results

    monkeypatch.setattr(suite, "_run_payload_batch", fake_batch)
    result = suite.run_libero_short_suite(
        seeds=1,
        workers=2,
        out_dir=tmp_path,
    )

    assert batches == [tasks[:8], tasks[8:16], tasks[16:]]
    assert result["status"] == "incomplete"
    assert result["episode_failures"] == 16
    assert result["unresolved_implementation_error_count"] == 1
    assert len((tmp_path / "episodes.jsonl").read_text().splitlines()) == 17


def test_parent_worker_error_preserves_resume_attempt() -> None:
    payload = {
        "task_key": "libero_goal_lan/0",
        "backend": "libero-pro",
        "seed": 12,
        "attempt_start": 4,
    }

    row = suite._parent_worker_error(payload, RuntimeError("worker exited"))

    assert row["task_key"] == "libero_goal_lan/0"
    assert row["backend"] == "libero-pro"
    assert row["seed"] == 12
    assert row["attempt"] == 4


def test_payload_batch_contains_child_exceptions() -> None:
    payloads = [
        {
            "task_key": f"libero_goal_lan/{index}",
            "backend": "libero-pro",
            "seed": 12,
            "attempt_start": index + 2,
        }
        for index in range(2)
    ]

    results = suite._run_payload_batch(payloads, workers=2)

    assert len(results) == 2
    rows_by_task = {
        payload["task_key"]: rows[0]
        for payload, rows in results
    }
    for payload in payloads:
        row = rows_by_task[payload["task_key"]]
        assert row["verdict"] == "implementation_error"
        assert row["attempt"] == payload["attempt_start"]
