from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi_libero.evidence import EvidenceConflict, replay_bundle


def _write_bundle(root: Path, records: list[dict]) -> Path:
    root.mkdir()
    manifest = {
        "schema": "roborsi.libero_short_evidence.v1",
        "metric": "task_level_adaptive_pass_at_k",
        "claim_scope": "adaptive_cross_release_development_coverage",
        "k": 2,
        "task_catalog": ["libero_spatial/0", "libero_goal/0"],
        "episodes": "episodes.jsonl",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "episodes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records),
        encoding="utf-8",
    )
    return root / "manifest.json"


def _record(task: str, seed: int, category: str) -> dict:
    return {
        "schema": "roborsi.libero_episode.v1",
        "task_key": task,
        "seed": seed,
        "attempt": 1,
        "release_id": f"release-{seed}",
        "category": category,
        "simulator_verdict": category if category.startswith("task_") else None,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "vlm_calls": 1,
        "elapsed_s": 3.0,
    }


def test_replay_counts_unique_task_success_and_excludes_infrastructure(tmp_path: Path) -> None:
    manifest = _write_bundle(
        tmp_path / "bundle",
        [
            _record("libero_spatial/0", 0, "task_failure"),
            _record("libero_goal/0", 0, "provider_failure"),
            _record("libero_spatial/0", 1, "task_success"),
        ],
    )

    result = replay_bundle(manifest)

    assert result.solved_tasks == 1
    assert result.total_tasks == 2
    assert result.rate == 0.5
    assert result.infrastructure_excluded == 1
    assert result.pass_curve == [0, 1]
    assert result.claim_scope == "adaptive_cross_release_development_coverage"


def test_replay_rejects_conflicting_terminal_verdicts(tmp_path: Path) -> None:
    success = _record("libero_spatial/0", 0, "task_success")
    failure = {**success, "category": "task_failure", "simulator_verdict": "task_failure"}
    manifest = _write_bundle(tmp_path / "bundle", [success, failure])

    with pytest.raises(EvidenceConflict, match="conflicting"):
        replay_bundle(manifest)


def test_replay_rejects_success_without_native_simulator_verdict(tmp_path: Path) -> None:
    success = _record("libero_spatial/0", 0, "task_success")
    success["simulator_verdict"] = None
    manifest = _write_bundle(tmp_path / "bundle", [success])

    with pytest.raises(ValueError, match="simulator verdict"):
        replay_bundle(manifest)


def test_replay_rejects_manifest_expected_result_mismatch(tmp_path: Path) -> None:
    manifest = _write_bundle(
        tmp_path / "bundle",
        [_record("libero_spatial/0", 0, "task_success")],
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["expected_result"] = {"solved_tasks": 2, "total_tasks": 2}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="expected_result"):
        replay_bundle(manifest)


def test_public_bundle_replays_locked_adaptive_95_of_120() -> None:
    manifest = (
        Path(__file__).resolve().parents[1]
        / "evidence/adaptive-pass10-v1/manifest.json"
    )

    result = replay_bundle(manifest)

    assert result.solved_tasks == 95
    assert result.total_tasks == 120
    assert result.rate == 95 / 120
    assert result.by_suite["libero_spatial"]["solved_tasks"] == 9
    assert result.by_suite["libero_object"]["solved_tasks"] == 10
    assert result.by_suite["libero_goal"]["solved_tasks"] == 9
    assert result.by_suite["libero_90"]["solved_tasks"] == 67
    assert result.claim_scope == "adaptive_cross_release_development_coverage"
