from __future__ import annotations

from types import SimpleNamespace

import pytest

from roborsi.libero.programs import execute_program, validate_program_source

pytestmark = pytest.mark.runtime


def test_program_parser_allows_only_literal_tool_sequence() -> None:
    accepted = validate_program_source(
        'PROGRAM = [{"tool": "look", "args": {"camera": "head"}}]\n',
        allowed_tools={"look"},
        program_name="adaptive_observe",
    )
    rejected = validate_program_source(
        "import os\nPROGRAM = []\n",
        allowed_tools={"look"},
        program_name="adaptive_observe",
    )

    assert accepted.ok
    assert not rejected.ok


def test_program_requires_calls_and_declared_argument_placeholders() -> None:
    empty = validate_program_source(
        "PROGRAM = []\n",
        allowed_tools={"look"},
        allowed_parameters=set(),
        program_name="adaptive_observe",
    )
    undeclared = validate_program_source(
        'PROGRAM = [{"tool": "look", "args": {"camera": "$camera"}}]\n',
        allowed_tools={"look"},
        allowed_parameters={"view"},
        program_name="adaptive_observe",
    )

    assert not empty.ok
    assert any("at least one" in finding for finding in empty.findings)
    assert not undeclared.ok
    assert any("$camera" in finding for finding in undeclared.findings)


def test_execute_program_expands_arguments_and_uses_visible_dispatch(
    monkeypatch,
) -> None:
    from roborsi.embodied.agent_loop import rollout

    calls = []

    def fake_dispatch(state, name, args):
        calls.append((name, args))
        return {"ok": True, "grasped": True}, "after"

    monkeypatch.setattr(rollout, "_dispatch_tool", fake_dispatch)
    state = SimpleNamespace(
        _allowed_tools={"grasp_object"},
        env=SimpleNamespace(take_snapshot=lambda: "before"),
    )

    result, observation = execute_program(
        [{"tool": "grasp_object", "args": {"object": "$object"}}],
        state,
        {"object": "alphabet soup"},
        program_name="adaptive_pick",
    )

    assert calls == [("grasp_object", {"object": "alphabet soup"})]
    assert result["ok"] is True
    assert result["grasped"] is True
    assert observation == "after"


def test_execute_program_fails_closed_when_argument_is_missing() -> None:
    state = SimpleNamespace(
        _allowed_tools={"grasp_object"},
        env=SimpleNamespace(take_snapshot=lambda: "before"),
    )

    result, observation = execute_program(
        [{"tool": "grasp_object", "args": {"object": "$object"}}],
        state,
        {},
        program_name="adaptive_pick",
    )

    assert result["ok"] is False
    assert result["failed_phase"] == "program_validation"
    assert "$object" in result["reason"]
    assert observation == "before"
