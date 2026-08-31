from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime


def test_runtime_registers_only_libero_backend() -> None:
    from roborsi.embodied.agent_loop.registry import list_backends

    assert list_backends() == ["libero"]


def test_runtime_defaults_to_gpt_responses_medium() -> None:
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _responses_reasoning_effort

    assert DEFAULT_MODEL == "responses/gpt-5.6-sol"
    assert _responses_reasoning_effort() == "medium"


def test_visible_tool_surface_excludes_hidden_simulator_truth(monkeypatch) -> None:
    from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs

    monkeypatch.setenv("ROBORSI_ATOMIC_COMPOUND", "1")
    names = {spec["function"]["name"] for spec in _build_tool_specs(task="libero_pick_place")}

    assert {"look", "find_pixel", "grasp_object", "place_object_in", "done"} <= names
    assert {
        "check_success",
        "check_task",
        "check_task_success",
        "describe_scene",
        "get_object_pose",
    }.isdisjoint(names)


def test_exported_runtime_contains_no_machine_specific_default() -> None:
    root = Path(__file__).resolve().parents[2] / "src/roborsi"
    private_paths = (
        re.compile(r"/data/[A-Za-z0-9._-]+/"),
        re.compile(r"/mnt/workspace/[A-Za-z0-9._-]+/"),
    )
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix in {".py", ".md"}]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for pattern in private_paths:
            assert not pattern.search(text), f"machine-specific path found in {path}"


def test_rollout_trace_does_not_persist_hidden_reasoning() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src/roborsi/embodied/agent_loop/rollout.py"
    )
    text = path.read_text(encoding="utf-8")

    assert '"reasoning": reasoning_text' not in text
    assert "reasoning_text[:600]" not in text
