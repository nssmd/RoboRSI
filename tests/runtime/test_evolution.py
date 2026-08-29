from __future__ import annotations

import json
from pathlib import Path

from roborsi_libero.config import ReleaseConfig
from roborsi_libero.evolution import process_pending_proposals, validate_proposal
from roborsi_libero.launcher import create_campaign


def _proposal(campaign: Path, *, proposal_id: str, code: str) -> Path:
    path = campaign / "proposals" / f"{proposal_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "roborsi.libero_skill_proposal.v1",
                "id": proposal_id,
                "kind": "update",
                "task": "libero_pick_place",
                "benchmark_task": "libero_object/0",
                "status": "pending",
                "payload": {
                    "name": "home",
                    "new_code": code,
                    "rationale": "bounded visible recovery",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_static_gate_rejects_hidden_simulator_success_access() -> None:
    proposal = {
        "kind": "update",
        "payload": {
            "name": "home",
            "new_code": (
                "def dispatch_runtime(state, args):\n"
                "    return state.env.check_success()\n"
            ),
        },
    }

    result = validate_proposal(proposal)

    assert not result.ok
    assert any("check_success" in finding for finding in result.findings)


def test_static_gate_rejects_indirect_private_environment_access() -> None:
    proposal = {
        "kind": "update",
        "payload": {
            "name": "home",
            "new_code": (
                "def dispatch_runtime(state, args):\n"
                "    raw = getattr(state.env, '_raw')\n"
                "    return ({'ok': bool(raw)}, None)\n"
            ),
        },
    }

    result = validate_proposal(proposal)

    assert not result.ok
    assert any("_raw" in finding for finding in result.findings)


def test_passed_harness_promotes_update_into_campaign_overlay(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    code = (
        "from __future__ import annotations\n\n"
        "def dispatch_runtime(state, args):\n"
        "    return ({'ok': True}, state.env.take_snapshot())\n"
    )
    proposal_path = _proposal(campaign, proposal_id="proposal-1", code=code)

    release = process_pending_proposals(
        campaign,
        seed=0,
        run_harness=lambda **kwargs: {
            "success": True,
            "category": "task_success",
            "simulator_verdict": "task_success",
        },
    )

    promoted = campaign / "workspace/embodied/skills/base/home/libero/policy.py"
    assert release == "adaptive-seed0-proposal-1"
    assert promoted.read_text(encoding="utf-8") == code
    record = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert record["status"] == "applied"
    assert record["harness_result"]["success"] is True


def test_failed_harness_preserves_candidate_without_promoting(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)
    campaign = create_campaign(config, mode="adaptive", run_id="run")
    code = "def dispatch_runtime(state, args):\n    return ({'ok': False}, None)\n"
    proposal_path = _proposal(campaign, proposal_id="proposal-2", code=code)

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
        campaign
        / "candidate_overlays/proposal-2/embodied/skills/base/home/libero/policy.py"
    )
    promoted = campaign / "workspace/embodied/skills/base/home/libero/policy.py"
    assert release is None
    assert candidate.is_file()
    assert not promoted.exists()
    assert json.loads(proposal_path.read_text(encoding="utf-8"))["status"] == "rejected_harness"
