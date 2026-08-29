from __future__ import annotations

from pathlib import Path


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


def test_exported_runtime_contains_no_non_gpt_provider_or_private_default() -> None:
    root = Path(__file__).resolve().parents[2] / "src/roborsi"
    forbidden = (
        "anthropic",
        "claude",
        "/mnt" + "/workspace",
        "/data" + "/yijia",
        "copilot" + "-proxy-local",
    )
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix in {".py", ".md"}]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for needle in forbidden:
            assert needle.lower() not in text, f"{needle!r} found in {path}"
