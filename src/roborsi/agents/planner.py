"""GT-isolated planning role for one LIBERO episode."""

from __future__ import annotations

import json
import re
from typing import Any

from roborsi.agents.workspace import Workspace
from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_no_tools


_SYSTEM = """You are the Planner for a single-arm LIBERO manipulation episode.
You receive only the visible runtime instruction and the registered tool names.
Produce a concise plan for a separate Engineer. Never invent coordinates,
simulator state, rewards, predicate logic, or completion status.

Return one JSON object followed by a markdown plan. JSON fields: goal,
sub_goals, success_criteria, candidate_skills, expected_steps, risks.
Every candidate skill must be from the supplied list. Preserve the exact task
verb and relation. All object and target geometry must come from current RGB-D.
"""


def _parse(response: str, task: str) -> tuple[dict[str, Any], str]:
    match = re.search(r"\{.*?\}", response, re.S)
    spec: dict[str, Any] = {}
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                spec = value
        except json.JSONDecodeError:
            pass
    markdown_match = re.search(r"```(?:markdown)?\s*(.*?)```", response, re.S)
    plan = markdown_match.group(1).strip() if markdown_match else response.strip()
    if not plan:
        plan = f"# Plan: {task}\n\nUse current RGB-D and registered skills to complete the instruction.\n"
    return spec, plan


class Planner:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL

    def plan(
        self,
        *,
        task: str,
        user_msg: str,
        recent_reflections: str,
        workspace: Workspace,
        ns: str = "libero",
    ) -> dict[str, Any]:
        if ns != "libero":
            raise ValueError(f"unsupported public skill namespace: {ns}")
        skills = [
            spec["function"]["name"]
            for spec in _build_tool_specs(ns="libero", task=task)
            if spec["function"]["name"] not in {
                "done",
                "read_skill_code",
                "list_base_skills",
                "propose_new_skill",
                "propose_skill_update",
            }
        ]
        prompt = (
            f"Task family: {task}\n"
            f"Visible runtime instruction: {user_msg}\n"
            f"Registered tools: {', '.join(skills)}\n"
            f"Visible prior reflections, if any: {recent_reflections or '(none)'}"
        )
        response = _call_vlm_no_tools(
            self.model,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        spec, plan = _parse(response, task)
        workspace.write_plan(plan)
        spec.setdefault("goal", user_msg)
        spec.setdefault("sub_goals", [])
        spec.setdefault("success_criteria", [])
        spec.setdefault("candidate_skills", [])
        spec.setdefault("expected_steps", 12)
        spec.setdefault("risks", [])
        return spec
