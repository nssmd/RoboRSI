from __future__ import annotations

import json

import pytest

from roborsi.agents.planner import Planner
from roborsi.agents.workspace import Workspace

pytestmark = pytest.mark.runtime


def test_planner_persists_validated_top_down_plan(monkeypatch, tmp_path) -> None:
    from roborsi.agents import planner as planner_module

    response = {
        "task_family": "wrong-family",
        "atomic_task": "wrong-atomic",
        "goal": "place the object",
        "expected_steps": "not-a-number",
        "steps": [
            {
                "id": "locate",
                "goal": "localize the source",
                "skills": ["find_pixel", "check_success"],
                "completion_evidence": ["current RGB"],
                "depends_on": [],
            },
            {
                "id": "locate",
                "goal": "place on the target",
                "skills": ["place_on_surface"],
                "completion_evidence": ["visible release"],
                "depends_on": ["locate", "future-step"],
            },
        ],
    }
    monkeypatch.setattr(
        planner_module,
        "_call_vlm_no_tools",
        lambda *args, **kwargs: json.dumps(response),
    )
    workspace = Workspace(task="libero_pick_place", run_id="run", root=tmp_path)

    result = Planner().plan(
        task="libero_pick_place",
        task_key="libero_object/0",
        atomic_skill="libero_object_00",
        user_msg="pick the alphabet soup and place it in the basket",
        recent_reflections="",
        workspace=workspace,
    )

    assert result["schema"] == "roborsi.top_down_plan.v1"
    assert result["task_family"] == "libero_pick_place"
    assert result["atomic_task"] == "libero_object_00"
    assert result["steps"][0]["skills"] == ["find_pixel"]
    assert result["steps"][1]["id"] == "step-2"
    assert result["steps"][1]["depends_on"] == ["locate"]
    assert result["expected_steps"] == 2
    assert workspace.read_plan_json() == result
    assert "Ordered Steps" in workspace.read_plan()
