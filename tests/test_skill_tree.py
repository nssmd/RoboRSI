from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi_libero.skill_tree import (
    SCHEMA,
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
    assert "type=\"range\"" in text
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
