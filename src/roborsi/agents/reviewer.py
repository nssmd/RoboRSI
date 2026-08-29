"""Independent visible-trace Reviewer for adaptive skill proposals."""

from __future__ import annotations

import json
import re
from typing import Any

from roborsi.agents.workspace import Workspace
from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_no_tools


_SYSTEM = """You are the independent Reviewer for a LIBERO manipulation attempt.
Use only the visible plan, Engineer summary, and visible tool trace. You do not
receive simulator reward, object state, predicate source, or final predicate
value. Diagnose repeated visible failures and suggest one materially different
next action.

Return one JSON object with verdict, root_cause, next_action,
proposal_decision, proposal_payload, and review_md. proposal_decision is one of
NO_PROPOSAL, SKILL_UPDATE, or NEW_SKILL. A code proposal must be a complete
camera/proprioception-only implementation; otherwise choose NO_PROPOSAL.
"""


def _parse(response: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", response, re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class Reviewer:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL

    def review(
        self,
        *,
        workspace: Workspace,
        engineer_result: dict[str, Any],
        run_id: str | None = None,
        ns: str = "libero",
        posthoc_behavior_review: bool = False,
    ) -> dict[str, Any]:
        del run_id, posthoc_behavior_review
        if ns != "libero":
            raise ValueError(f"unsupported public skill namespace: {ns}")
        trace = engineer_result.get("trace") or []
        packet = {
            "plan": workspace.read_plan()[:8000],
            "summary": workspace.read_summary()[:4000],
            "engineer_visible_outcome": engineer_result.get("outcome"),
            "engineer_declared_completion": engineer_result.get("success"),
            "trace": trace[-20:],
        }
        response = _call_vlm_no_tools(
            self.model,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=True, default=str)},
            ],
        )
        review = _parse(response)
        review.setdefault("verdict", "blocked")
        review.setdefault("root_cause", "review output could not be parsed")
        review.setdefault("next_action", "")
        review.setdefault("proposal_decision", "NO_PROPOSAL")
        review.setdefault("proposal_payload", {})
        review.setdefault("review_md", response[:1000])
        if review["proposal_decision"] != "NO_PROPOSAL":
            try:
                from roborsi.embodied.agent_loop.prompt_tools import _queue_proposal

                kind = "new" if review["proposal_decision"] == "NEW_SKILL" else "update"
                review["proposal_id"] = _queue_proposal(
                    kind,
                    dict(review.get("proposal_payload") or {}),
                    workspace.task,
                )
            except Exception as exc:  # noqa: BLE001
                review["proposal_decision"] = "NO_PROPOSAL"
                review["proposal_error"] = f"{type(exc).__name__}: {exc}"
        workspace.write_review(
            f"# Review: {workspace.task}\n\n"
            f"Verdict: {review['verdict']}\n\n"
            f"Root cause: {review['root_cause']}\n\n"
            f"Next action: {review['next_action']}\n"
        )
        return review
