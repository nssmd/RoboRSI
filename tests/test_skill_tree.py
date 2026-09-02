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
from roborsi.libero.skill_tree import (
    CAMPAIGN_SCHEMA,
    SCHEMA,
    build_campaign_skill_tree_html,
    load_campaign_skill_tree,
    load_storyboard,
    sanitize_storyboard,
    validate_storyboard,
    write_skill_tree_html,
)


def _storyboard() -> dict:
    return {
        "schema_version": SCHEMA,
        "title": "SKILL TREE",
        "subtitle": "ROBORSI SELF-EVOLUTION / ONE TASK",
        "task": "TIDY TASK",
        "round_count": 2,
        "events": [
            {
                "round": 1,
                "emphasis_label": "NEW CAPABILITY",
                "headline": "Perception appears",
                "summary": "The agent creates a visible perception skill.",
                "change": "A reusable skill is added.",
                "mode": "TASK EXECUTION",
                "atomics": ["sweep"],
                "bases": ["capture_seg"],
                "branch_additions": ["capture_seg"],
                "finalizes": [],
            },
            {
                "round": 2,
                "emphasis_label": "TASK SUCCESS",
                "headline": "Capability stabilizes",
                "summary": "The task completes with retained evidence.",
                "change": "The verified skill is promoted.",
                "mode": "VALIDATION",
                "atomics": ["sweep"],
                "bases": ["capture_seg"],
                "branch_additions": [],
                "finalizes": ["sweep", "capture_seg", "task"],
            },
        ],
    }


def test_sanitize_storyboard_removes_internal_run_metadata() -> None:
    payload = _storyboard()
    payload["schema_version"] = "legacy.skill-tree-storyboard.v3"
    payload["events"][0]["source_runtime_id"] = "private-run"
    payload["events"][0]["repair_ids"] = ["internal-repair"]

    result = sanitize_storyboard(payload)

    assert result["schema_version"] == SCHEMA
    assert result["subtitle"].startswith("ROBORSI")
    assert "source_runtime_id" not in result["events"][0]
    assert "repair_ids" not in result["events"][0]


def test_validate_storyboard_rejects_nonconsecutive_rounds() -> None:
    payload = _storyboard()
    payload["events"][1]["round"] = 3

    with pytest.raises(ValueError, match="consecutive"):
        validate_storyboard(payload)


def test_write_skill_tree_html_is_standalone_and_interactive(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.json"
    storyboard.write_text(json.dumps(_storyboard()), encoding="utf-8")

    output = write_skill_tree_html(
        tmp_path / "skill-tree.html",
        storyboard_path=storyboard,
    )
    text = output.read_text(encoding="utf-8")

    assert "ROBORSI SELF-EVOLUTION" in text
    assert 'type="range"' in text
    assert "Perception appears" in text
    assert "https://" not in text


def test_default_storyboard_is_packaged_and_private_metadata_free() -> None:
    payload = load_storyboard()

    assert payload["schema_version"] == SCHEMA
    assert payload["round_count"] == 104
    assert all(
        "source_runtime_id" not in event and "repair_ids" not in event
        for event in payload["events"]
    )


def test_campaign_skill_tree_uses_retained_plan_and_verdict(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    roles = campaign / "episodes/run/libero_object__0/seed-0/shard-0/attempt-1/roles"
    roles.mkdir(parents=True)
    (roles / "plan.json").write_text(
        json.dumps(
            {
                "schema": "roborsi.top_down_plan.v1",
                "task_key": "libero_object/0",
                "task_family": "libero_pick_place",
                "atomic_task": "libero_object_00",
                "steps": [
                    {
                        "id": "pick",
                        "goal": "pick the visible object",
                        "skills": ["grasp_object"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    append_record(
        campaign / "journals/seed-0-worker-0.episodes.jsonl",
        EpisodeRecord(
            identity=EpisodeIdentity(
                run_id="run",
                task_key="libero_object/0",
                seed=0,
                shard=0,
                attempt=1,
            ),
            category="task_success",
            success=True,
            outcome="visible",
            release_id="release-1",
        ),
    )

    payload = load_campaign_skill_tree(campaign, task_key="libero_object/0")
    html = build_campaign_skill_tree_html(payload)

    assert payload["schema_version"] == CAMPAIGN_SCHEMA
    assert payload["rounds"][0]["verdict"] == "task_success"
    assert "libero_object_00" in html
    assert "grasp_object" in html
    assert 'type="range"' in html
    assert "innerHTML" not in html


def test_campaign_skill_tree_escapes_static_identity_fields() -> None:
    html = build_campaign_skill_tree_html(
        {
            "schema_version": CAMPAIGN_SCHEMA,
            "run_id": "<img src=x onerror=alert(1)>",
            "task_key": "<script>alert(1)</script>",
            "task_family": "libero_pick_place",
            "atomic_task": "libero_object_00",
            "rounds": [
                {
                    "round": 1,
                    "seed": 0,
                    "attempt": 1,
                    "release_id": "",
                    "verdict": "running",
                    "steps": [],
                }
            ],
            "promotions": [],
        }
    )

    assert "<img src=x" not in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;img src=x" in html
