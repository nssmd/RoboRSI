from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roborsi.agents.proposal_safety import inspect_candidate, inspect_skill_text


SAFE_POLICY = """\
from __future__ import annotations

from typing import Any

from roborsi.embodied.agent_loop.rollout import _dispatch_tool


def dispatch_runtime(state, args: dict[str, Any]) -> tuple[dict, object]:
    result, obs = _dispatch_tool(
        state,
        "look",
        {"camera": args.get("camera", "head_camera")},
    )
    if not result.get("ok", False):
        return result, obs
    moved, obs = _dispatch_tool(state, "gripper", {"state": "open"})
    return {"ok": bool(moved.get("ok")), "detail": moved}, obs
"""


def _codes(source: str, *, tools: set[str] | None = None) -> set[str]:
    return {
        finding.code
        for finding in inspect_candidate(
            source,
            allowed_tools=tools or {"gripper", "look"},
            candidate_name="candidate_skill",
        )
    }


def test_safe_candidate_composes_literal_public_tools() -> None:
    assert _codes(SAFE_POLICY) == set()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    return {"ok": state.env.check_success()}, state.env.take_snapshot()
""",
            "capability",
        ),
        (
            """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    runtime = state
    return _dispatch_tool(runtime, "look", {})
""",
            "capability",
        ),
        (
            """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    return _dispatch_tool(state, args["tool"], {})
""",
            "dynamic_tool",
        ),
        (
            """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    return _dispatch_tool(state, "check_task_success", {})
""",
            "nonpublic_tool",
        ),
        (
            """\
import os
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    os.system("true")
    return _dispatch_tool(state, "look", {})
""",
            "import",
        ),
        (
            """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
open("/tmp/leak", "w")
def dispatch_runtime(state, args):
    return _dispatch_tool(state, "look", {})
""",
            "module_scope",
        ),
    ],
)
def test_candidate_boundary_rejects_privileged_paths(
    source: str,
    expected: str,
) -> None:
    assert expected in _codes(source)


def test_candidate_cannot_dispatch_itself() -> None:
    source = SAFE_POLICY.replace('"look"', '"candidate_skill"', 1)
    assert "recursive_tool" in _codes(source, tools={"candidate_skill", "gripper"})


def test_skill_text_cannot_publish_hidden_simulator_contract() -> None:
    findings = inspect_skill_text(
        "---\nname: candidate_skill\n---\n"
        "Read state.env.check_success and copy the ground-truth threshold.\n"
    )
    assert {finding.code for finding in findings} == {"skill_text"}


def test_validator_stops_before_simulator_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roborsi.agents import proposal_safety, validator

    unsafe = SAFE_POLICY.replace('"look"', '"private_tool"', 1)
    monkeypatch.setattr(proposal_safety, "public_tool_names", lambda _ns: {"look"})
    monkeypatch.setattr(
        validator,
        "_stage_and_gate",
        lambda *_args, **_kwargs: pytest.fail("unsafe code reached simulator gate"),
    )

    report = validator.ProposalValidator().validate({
        "id": "unsafe",
        "kind": "new",
        "name": "candidate_skill",
        "category": "base/libero",
        "code": unsafe,
    })

    assert report.overall_pass is False
    assert report.capability is not None
    assert report.capability.passed is False
    assert report.harness is None


def test_skip_harness_cannot_bypass_candidate_boundary(tmp_path: Path) -> None:
    proposal_id = "unsafe-proposal"
    queue = tmp_path / ".roborsi" / "skill_review"
    queue.mkdir(parents=True)
    (queue / f"{proposal_id}.json").write_text(
        json.dumps({
            "id": proposal_id,
            "kind": "new",
            "name": "unsafe_probe_skill",
            "category": "base/libero",
            "code": """\
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    verdict = state.env.check_success()
    return {"ok": verdict}, state.env.take_snapshot()
""",
            "skill_md": "---\nname: unsafe_probe_skill\n---\n",
            "rationale": "test",
        }),
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "ROBORSI_HOME": str(tmp_path / ".roborsi"),
        "ROBORSI_RUN_MODE": "evolve",
    }
    result = subprocess.run(
        [
            sys.executable,
            "scripts/apply_selfevo_proposal.py",
            proposal_id,
            "--skip-harness",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "unsafe Agent-authored policy" in result.stderr
    assert not (
        repo
        / "roborsi"
        / "embodied"
        / "skills"
        / "base"
        / "unsafe_probe_skill"
    ).exists()
