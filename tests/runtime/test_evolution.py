from __future__ import annotations

import json
from pathlib import Path

import pytest

from roborsi.libero.config import ReleaseConfig
from roborsi.libero.evolution import process_pending_proposals, validate_proposal
from roborsi.libero.launcher import create_campaign

pytestmark = pytest.mark.runtime


def _skill_md(name: str) -> str:
    return f"""---
name: {name}
kind: compound
parent: libero_pick_place
domain: manipulation
version: 0.1.0
description: Safe adaptive composition.
args:
  object: {{type: string, required: true}}
returns:
  ok: bool
metadata:
  tags: [compound, adaptive, pure-vision]
  backends: [libero, libero-pro]
  runtime_status: code-backed
  compound: true
---

# {name}
"""


def _proposal(campaign: Path, *, proposal_id: str, program_code: str) -> Path:
    path = campaign / "proposals" / f"{proposal_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "roborsi.libero_skill_proposal.v1",
                "id": proposal_id,
                "kind": "new",
                "task": "libero_pick_place",
                "benchmark_task": "libero_object/0",
                "status": "pending",
                "payload": {
                    "name": "adaptive_pick",
                    "description": "safe composition",
                    "program_code": program_code,
                    "skill_md": _skill_md("adaptive_pick"),
                    "rationale": "reuse visible tools",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_static_gate_rejects_arbitrary_python_and_dynamic_gt_access() -> None:
    proposal = {
        "kind": "new",
        "task": "libero_pick_place",
        "payload": {
            "name": "adaptive_pick",
            "program_code": (
                "def dispatch_runtime(state, args):\n"
                "    name = ''.join(['check', '_success'])\n"
                "    return getattr(state.env, name)()\n"
            ),
            "skill_md": _skill_md("adaptive_pick"),
        },
    }

    result = validate_proposal(proposal)

    assert not result.ok
    assert any("PROGRAM literal" in finding for finding in result.findings)


def test_static_gate_accepts_published_tool_program() -> None:
    proposal = {
        "kind": "new",
        "task": "libero_pick_place",
        "payload": {
            "name": "adaptive_pick",
            "program_code": (
                'PROGRAM = [{"tool": "grasp_object", "args": {"object": "$object"}}]\n'
            ),
            "skill_md": _skill_md("adaptive_pick"),
        },
    }

    result = validate_proposal(proposal)

    assert result.ok
    assert result.findings == ()


def test_passed_harness_promotes_safe_compound_into_campaign_overlay(
    tmp_path: Path,
) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    proposal_path = _proposal(
        campaign,
        proposal_id="proposal-1",
        program_code=('PROGRAM = [{"tool": "grasp_object", "args": {"object": "$object"}}]\n'),
    )

    release = process_pending_proposals(
        campaign,
        seed=0,
        run_harness=lambda **kwargs: {
            "success": True,
            "category": "task_success",
            "simulator_verdict": "task_success",
        },
    )

    promoted = campaign / "workspace/embodied/skills/compound/libero/adaptive_pick/program.py"
    policy = promoted.with_name("policy.py")
    assert release == "adaptive-seed0-proposal-1"
    assert '"tool": "grasp_object"' in promoted.read_text(encoding="utf-8")
    assert "execute_program" in policy.read_text(encoding="utf-8")
    record = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert record["status"] == "applied"
    assert record["validation_seeds"] == [0, 1]
    assert len(record["harness_results"]) == 2


def test_failed_harness_preserves_candidate_without_promoting(
    tmp_path: Path,
) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    proposal_path = _proposal(
        campaign,
        proposal_id="proposal-2",
        program_code='PROGRAM = [{"tool": "look", "args": {"camera": "head"}}]\n',
    )

    release = process_pending_proposals(
        campaign,
        seed=0,
        run_harness=lambda **kwargs: {
            "success": False,
            "category": "task_failure",
            "simulator_verdict": "task_failure",
        },
    )

    candidate = (
        campaign / "candidate_overlays/proposal-2/embodied/skills/compound/libero/"
        "adaptive_pick/program.py"
    )
    promoted = campaign / "workspace/embodied/skills/compound/libero/adaptive_pick/program.py"
    assert release is None
    assert candidate.is_file()
    assert not promoted.exists()
    assert json.loads(proposal_path.read_text(encoding="utf-8"))["status"] == ("rejected_harness")


def test_promotion_requires_current_and_holdout_success(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    proposal_path = _proposal(
        campaign,
        proposal_id="proposal-3",
        program_code='PROGRAM = [{"tool": "look", "args": {"camera": "head"}}]\n',
    )
    calls = []

    def harness(**kwargs):
        calls.append(kwargs["seed"])
        success = kwargs["seed"] == 0
        return {
            "success": success,
            "category": "task_success" if success else "task_failure",
            "simulator_verdict": "task_success" if success else "task_failure",
        }

    release = process_pending_proposals(campaign, seed=0, run_harness=harness)

    assert release is None
    assert calls == [0, 1]
    record = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert record["status"] == "rejected_harness"


def test_infrastructure_interruption_retries_only_unfinished_holdout(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    proposal_path = _proposal(
        campaign,
        proposal_id="proposal-4",
        program_code='PROGRAM = [{"tool": "look", "args": {"camera": "head"}}]\n',
    )
    calls = []
    interrupted = True

    def harness(**kwargs):
        nonlocal interrupted
        calls.append(kwargs["seed"])
        if kwargs["seed"] == 1 and interrupted:
            interrupted = False
            return {
                "success": None,
                "category": "provider_failure",
                "simulator_verdict": None,
            }
        return {
            "success": True,
            "category": "task_success",
            "simulator_verdict": "task_success",
        }

    first = process_pending_proposals(campaign, seed=0, run_harness=harness)
    pending = json.loads(proposal_path.read_text(encoding="utf-8"))
    second = process_pending_proposals(campaign, seed=4, run_harness=harness)
    applied = json.loads(proposal_path.read_text(encoding="utf-8"))

    assert first is None
    assert pending["status"] == "pending"
    assert pending["validation_status"] == "infrastructure_interrupted"
    assert pending["validation_seeds"] == [0, 1]
    assert second == "adaptive-seed0-proposal-4"
    assert calls == [0, 1, 1]
    assert applied["status"] == "applied"
    assert applied["candidate_release_id"] == "adaptive-seed0-proposal-4"


def test_static_gate_rejects_undeclared_program_argument() -> None:
    proposal = {
        "kind": "new",
        "task": "libero_pick_place",
        "payload": {
            "name": "adaptive_pick",
            "program_code": (
                'PROGRAM = [{"tool": "grasp_object", "args": {"object": "$target"}}]\n'
            ),
            "skill_md": _skill_md("adaptive_pick"),
        },
    }

    result = validate_proposal(proposal)

    assert not result.ok
    assert any("$target" in finding for finding in result.findings)
