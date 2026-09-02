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
Build a top-down executable Skill Tree for a separate Engineer:
Task Family -> Atomic Task -> ordered plan steps -> Base or Compound Skills.

You receive only public skill descriptions and the visible runtime instruction.
Never invent coordinates, simulator state, rewards, predicate logic, or task
completion. Preserve the exact task verb and relation. All object and target
geometry must come from current RGB-D.

Return one JSON object followed by a short markdown plan. JSON fields:
task_family, atomic_task, goal, steps, success_criteria, risks.
Each step contains id, goal, skills, completion_evidence, and depends_on.
Every skill must be selected from the supplied capability list.
"""


def _json_object(response: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", response):
        try:
            value, _ = decoder.raw_decode(response[match.start() :])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return {}


def _parse(response: str, task: str) -> tuple[dict[str, Any], str]:
    spec = _json_object(response)
    markdown_match = re.search(r"```(?:markdown)?\s*(.*?)```", response, re.S)
    plan = markdown_match.group(1).strip() if markdown_match else response.strip()
    if not plan:
        plan = (
            f"# Plan: {task}\n\n"
            "Use current RGB-D and registered skills to complete the instruction.\n"
        )
    return spec, plan


def _recommended_family_skills(task_family: str) -> list[str]:
    from roborsi.embodied.skills import discover_executors

    names: list[str] = []
    for executor in discover_executors(task_family, backend="libero"):
        metadata = executor.frontmatter.get("metadata") or {}
        for name in metadata.get("base_tools") or []:
            value = str(name)
            if value not in names:
                names.append(value)
    return names


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, default)


def _normalize_plan(
    spec: dict[str, Any],
    *,
    task_family: str,
    atomic_skill: str,
    task_key: str,
    user_msg: str,
    allowed_skills: set[str],
) -> dict[str, Any]:
    steps = []
    step_ids: set[str] = set()
    raw_steps = spec.get("steps")
    if isinstance(raw_steps, list):
        for index, raw in enumerate(raw_steps, 1):
            if not isinstance(raw, dict):
                continue
            selected = [
                name
                for name in _normalize_string_list(raw.get("skills") or raw.get("candidate_skills"))
                if name in allowed_skills
            ]
            goal = str(raw.get("goal") or raw.get("sub_goal") or "").strip()
            if not goal:
                continue
            step_id = str(raw.get("id") or f"step-{index}").strip() or f"step-{index}"
            if step_id in step_ids:
                step_id = f"step-{index}"
            depends_on = [
                value
                for value in _normalize_string_list(raw.get("depends_on"))
                if value in step_ids
            ]
            steps.append(
                {
                    "id": step_id,
                    "goal": goal,
                    "skills": selected,
                    "completion_evidence": _normalize_string_list(raw.get("completion_evidence")),
                    "depends_on": depends_on,
                }
            )
            step_ids.add(step_id)

    if not steps:
        sub_goals = _normalize_string_list(spec.get("sub_goals"))
        candidates = [
            name
            for name in _normalize_string_list(spec.get("candidate_skills"))
            if name in allowed_skills
        ]
        for index, goal in enumerate(sub_goals, 1):
            selected = candidates[index - 1 : index] or candidates
            steps.append(
                {
                    "id": f"step-{index}",
                    "goal": goal,
                    "skills": selected,
                    "completion_evidence": [],
                    "depends_on": [] if index == 1 else [f"step-{index - 1}"],
                }
            )

    if not steps:
        recommended = [
            name for name in _recommended_family_skills(task_family) if name in allowed_skills
        ]
        steps = [
            {
                "id": "step-1",
                "goal": "Complete the visible instruction using current RGB-D.",
                "skills": recommended,
                "completion_evidence": [
                    "Use visible camera or proprioceptive evidence before declaring completion."
                ],
                "depends_on": [],
            }
        ]

    candidate_skills = []
    for step in steps:
        for name in step["skills"]:
            if name not in candidate_skills:
                candidate_skills.append(name)
    return {
        "schema": "roborsi.top_down_plan.v1",
        "task_key": task_key,
        "task_family": task_family,
        "atomic_task": atomic_skill,
        "goal": str(spec.get("goal") or user_msg).strip(),
        "steps": steps,
        "success_criteria": _normalize_string_list(spec.get("success_criteria")),
        "candidate_skills": candidate_skills,
        "expected_steps": _positive_int(spec.get("expected_steps"), len(steps)),
        "risks": _normalize_string_list(spec.get("risks")),
    }


def _markdown_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"# Plan: {plan['atomic_task']}",
        "",
        f"- Task Family: `{plan['task_family']}`",
        f"- Atomic Task: `{plan['atomic_task']}`",
        f"- Goal: {plan['goal']}",
        "",
        "## Ordered Steps",
        "",
    ]
    for step in plan["steps"]:
        skills = ", ".join(f"`{name}`" for name in step["skills"]) or "visible tools"
        lines.append(f"1. **{step['id']}** - {step['goal']} ({skills})")
    return "\n".join(lines) + "\n"


class Planner:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL

    def plan(
        self,
        *,
        task: str,
        task_key: str,
        atomic_skill: str,
        user_msg: str,
        recent_reflections: str,
        workspace: Workspace,
        ns: str = "libero",
    ) -> dict[str, Any]:
        if ns != "libero":
            raise ValueError(f"unsupported public skill namespace: {ns}")
        tool_specs = [
            spec
            for spec in _build_tool_specs(ns="libero", task=task)
            if spec["function"]["name"]
            not in {
                "done",
                "read_skill_code",
                "list_base_skills",
                "propose_new_skill",
                "propose_skill_update",
            }
        ]
        skills = [spec["function"]["name"] for spec in tool_specs]
        capabilities = [
            {
                "name": spec["function"]["name"],
                "description": str(spec["function"].get("description") or "")[:500],
            }
            for spec in tool_specs
        ]
        prompt = (
            f"Task family: {task}\n"
            f"Atomic task: {atomic_skill}\n"
            f"Benchmark task key: {task_key}\n"
            f"Visible runtime instruction: {user_msg}\n"
            "Available capabilities:\n"
            f"{json.dumps(capabilities, ensure_ascii=True)}\n"
            f"Visible prior reflections, if any: {recent_reflections or '(none)'}"
        )
        response = _call_vlm_no_tools(
            self.model,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        raw_spec, _ = _parse(response, task)
        spec = _normalize_plan(
            raw_spec,
            task_family=task,
            atomic_skill=atomic_skill,
            task_key=task_key,
            user_msg=user_msg,
            allowed_skills=set(skills),
        )
        workspace.write_plan_json(spec)
        workspace.write_plan(_markdown_plan(spec))
        return spec
