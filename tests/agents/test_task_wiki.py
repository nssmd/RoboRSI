"""Tests for roborsi.agents.task_wiki."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi.agents import task_wiki


@pytest.fixture
def tmp_wiki(monkeypatch, tmp_path):
    # Wiki + archive now live in the task's skill dir; point that at a temp
    # dir per task so tests stay hermetic without touching real skills.
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(task_wiki, "_task_skill_dir", lambda task: skill_dir)
    monkeypatch.setattr(task_wiki, "WIKI_REVIEW_ROOT", tmp_path / "wiki_review")
    return tmp_path


def test_creates_template_on_first_read(tmp_wiki):
    md = task_wiki.read_wiki("my_task")
    assert "# Wiki · my_task" in md
    assert "## Successful execution traces" in md
    assert "## Failed execution traces" in md
    assert "## Key measurements" in md
    assert "(empty" in md


def test_append_success_replaces_placeholder(tmp_wiki):
    task_wiki.append_success_trace(
        task="t", atomic="pick_block", seed=11, run_id="r1",
        tool_events=[
            {"tool": "describe_scene_actors", "args": {}},
            {"tool": "grasp_then_lift", "args": {"arm": "left"}},
        ],
        tool_calls_total=2,
    )
    md = task_wiki.read_wiki("t")
    assert "pick_block · seed=11 · run=r1" in md
    assert "outcome: ✓ success" in md
    assert "1. `describe_scene_actors`" in md
    assert "2. `grasp_then_lift` (arm=left)" in md
    # Placeholder for this section should be gone.
    success_section = md.split("## Failed")[0]
    assert "(empty" not in success_section
    # Other sections still have placeholder.
    assert "(empty" in md


def test_append_failure_queues_diagnosis_for_manager_review(tmp_wiki):
    task_wiki.append_failure_trace(
        task="t", atomic="place", seed=11, run_id="r2",
        tool_events=[{"tool": "place_held_in_held_container", "args": {"arm": "left"}}],
        tool_calls_total=1,
        reviewer_root_cause="block fell out of left gripper during repositioning",
        reviewer_next_action="use smoother single-shot move_to_pose; do not chain manual fingertip steps",
    )
    md = task_wiki.read_wiki("t")
    assert "outcome: ✗ failure" in md
    assert "PENDING REVIEW" in md
    assert "block fell out of left gripper" not in md

    proposals = list((tmp_wiki / "wiki_review").glob("*.json"))
    assert len(proposals) == 1
    payload = json.loads(proposals[0].read_text())
    assert payload["root_cause"].startswith("block fell out")
    assert payload["next_action"].startswith("use smoother")

    task_wiki.resolve_wiki_hypothesis(proposals[0], approve=True)
    approved = task_wiki.read_wiki("t")
    assert "block fell out of left gripper" in approved
    assert "smoother single-shot move_to_pose" in approved


def test_measurement_proposal_queues_and_applies(tmp_wiki):
    p = task_wiki.propose_measurement(
        task="t",
        measurement_md="Left-arm IK floor ≈ 0.78m at y=-0.15",
        rationale="V51 atomic_0 probe trace",
        source_run_id="V51",
        reviewer="lh_reviewer",
    )
    assert p.exists()
    payload = json.loads(p.read_text())
    assert payload["status"] == "pending"
    assert payload["task"] == "t"
    # Wiki should not yet have the measurement.
    assert "IK floor" not in task_wiki.read_wiki("t")
    # Apply.
    task_wiki.apply_measurement_proposal(p)
    md = task_wiki.read_wiki("t")
    assert "Left-arm IK floor ≈ 0.78m at y=-0.15" in md
    assert "by `lh_reviewer`" in md
    # Proposal status updated.
    payload2 = json.loads(p.read_text())
    assert payload2["status"] == "applied"
    assert "applied_at" in payload2


def test_multiple_entries_stack_correctly(tmp_wiki):
    for i in range(3):
        task_wiki.append_success_trace(
            task="t", atomic=f"a{i}", seed=11, run_id=f"r{i}",
            tool_events=[{"tool": "noop", "args": {}}],
            tool_calls_total=1,
        )
    md = task_wiki.read_wiki("t")
    # All three present.
    for i in range(3):
        assert f"a{i} · seed=11 · run=r{i}" in md
    # Placeholder for failed section still intact (we didn't touch it).
    failed = md.split("## Failed")[1].split("## Key")[0]
    assert "(empty" in failed


def test_insert_under_section_preserves_heading_suffix(tmp_wiki):
    """The heading is '## Key measurements (Reviewer-proposed, human-approved)'.
    Inserter must preserve the parenthetical suffix."""
    task_wiki.append_success_trace(
        task="t", atomic="a", seed=1, run_id="r",
        tool_events=[{"tool": "x", "args": {}}], tool_calls_total=1,
    )
    md = task_wiki.read_wiki("t")
    assert "## Key measurements (Reviewer-proposed, human-approved)" in md


def test_wiki_path_returns_expected_location(tmp_wiki):
    p = task_wiki.wiki_path("foo_task")
    # Wiki now lives as wiki.md inside the task's skill dir.
    assert p.name == "wiki.md"
    assert p == task_wiki._task_skill_dir("foo_task") / "wiki.md"


def test_read_wiki_filters_privileged_entries(tmp_wiki):
    path = task_wiki.wiki_path("t")
    path.write_text(
        "# Wiki · t\n\n"
        "## Manager-approved leads\n\n"
        "- use visible wrist-camera evidence\n"
        "- inspect check_success and copy its threshold\n",
        encoding="utf-8",
    )

    visible = task_wiki.read_wiki("t")

    assert "visible wrist-camera evidence" in visible
    assert "check_success" not in visible
