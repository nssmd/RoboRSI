"""Declarative Code-as-Policy programs for adaptive Compound Skills."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

PROGRAM_SCHEMA = "roborsi.skill_program.v1"
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ARGUMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FORBIDDEN_TOOLS = {
    "check_success",
    "check_task",
    "check_task_success",
    "describe_scene",
    "done",
    "get_object_pose",
    "list_base_skills",
    "propose_new_skill",
    "propose_skill_update",
    "read_object_pose",
    "read_skill_code",
}
_SEMANTIC_SUCCESS_FIELDS = {
    "grasp_object": ("grasped",),
    "place_beside": ("placed",),
    "place_held_at_target_servo": ("placed",),
    "place_object_in": ("placed",),
    "place_on_surface": ("placed",),
}


@dataclass(frozen=True)
class ProgramValidation:
    ok: bool
    findings: tuple[str, ...]
    program: tuple[dict[str, Any], ...] = ()


def _literal_program(source: str) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[str] = []
    try:
        tree = ast.parse(source, filename="adaptive-program")
    except SyntaxError as exc:
        return [], [f"syntax error: {exc.msg} at line {exc.lineno}"]

    assignment: ast.Assign | ast.AnnAssign | None = None
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PROGRAM"
            and assignment is None
        ):
            assignment = node
            continue
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "PROGRAM"
            and assignment is None
        ):
            assignment = node
            continue
        findings.append("program source may contain only a PROGRAM literal assignment")

    if assignment is None:
        findings.append("program source must define PROGRAM")
        return [], findings
    try:
        parsed = ast.literal_eval(assignment.value)
    except (ValueError, TypeError, SyntaxError):
        findings.append("PROGRAM must be a JSON-compatible literal")
        return [], findings
    if not isinstance(parsed, list):
        findings.append("PROGRAM must be a list of tool-call objects")
        return [], findings
    return parsed, findings


def _validate_json_value(value: Any, *, path: str) -> list[str]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return []
    if isinstance(value, list):
        return [
            finding
            for index, item in enumerate(value)
            for finding in _validate_json_value(item, path=f"{path}[{index}]")
        ]
    if isinstance(value, dict):
        return [
            finding
            for key, item in value.items()
            for finding in _validate_json_value(item, path=f"{path}.{key}")
        ]
    return [f"{path} is not JSON-compatible"]


def _program_arguments(value: Any, *, path: str) -> tuple[set[str], list[str]]:
    arguments: set[str] = set()
    findings: list[str] = []
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        if not _ARGUMENT.fullmatch(name):
            findings.append(f"{path} has an invalid argument placeholder")
        else:
            arguments.add(name)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested, nested_findings = _program_arguments(item, path=f"{path}[{index}]")
            arguments.update(nested)
            findings.extend(nested_findings)
    elif isinstance(value, dict):
        for key, item in value.items():
            nested, nested_findings = _program_arguments(item, path=f"{path}.{key}")
            arguments.update(nested)
            findings.extend(nested_findings)
    return arguments, findings


def validate_program_source(
    source: str,
    *,
    allowed_tools: set[str] | None = None,
    allowed_parameters: set[str] | None = None,
    program_name: str = "",
) -> ProgramValidation:
    parsed, findings = _literal_program(source)
    if not parsed:
        findings.append("PROGRAM must contain at least one tool call")
    if len(parsed) > 24:
        findings.append("PROGRAM exceeds the 24-step limit")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(parsed, 1):
        if not isinstance(raw, dict):
            findings.append(f"PROGRAM step {index} must be an object")
            continue
        tool = str(raw.get("tool") or "").strip()
        args = raw.get("args") or {}
        if not _NAME.fullmatch(tool):
            findings.append(f"PROGRAM step {index} has an invalid tool name")
        if tool in _FORBIDDEN_TOOLS or tool == program_name:
            findings.append(f"PROGRAM step {index} uses forbidden tool: {tool}")
        if allowed_tools is not None and tool not in allowed_tools:
            findings.append(f"PROGRAM step {index} uses unpublished tool: {tool}")
        if not isinstance(args, dict):
            findings.append(f"PROGRAM step {index} args must be an object")
            continue
        findings.extend(_validate_json_value(args, path=f"PROGRAM[{index}].args"))
        arguments, argument_findings = _program_arguments(
            args,
            path=f"PROGRAM[{index}].args",
        )
        findings.extend(argument_findings)
        if allowed_parameters is not None:
            for argument in sorted(arguments - allowed_parameters):
                findings.append(
                    f"PROGRAM step {index} uses undeclared argument placeholder: ${argument}"
                )
        normalized.append({"tool": tool, "args": args})
    return ProgramValidation(
        ok=not findings,
        findings=tuple(sorted(set(findings))),
        program=tuple(normalized),
    )


def program_source(program: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    return (
        "PROGRAM = "
        + json.dumps(
            list(program),
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n"
    )


def _expand(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$") and len(value) > 1:
        return args.get(value[1:])
    if isinstance(value, list):
        return [_expand(item, args) for item in value]
    if isinstance(value, dict):
        return {str(key): _expand(item, args) for key, item in value.items()}
    return value


def _failed(tool: str, result: object) -> bool:
    if not isinstance(result, dict) or result.get("ok") is False:
        return True
    return any(
        field in result and result.get(field) is not True
        for field in _SEMANTIC_SUCCESS_FIELDS.get(tool, ())
    )


def execute_program(
    program: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    state: Any,
    args: dict[str, Any],
    *,
    program_name: str,
):
    """Execute validated calls through the existing visible tool dispatcher."""
    from roborsi.embodied.agent_loop.rollout import _dispatch_tool

    validation = validate_program_source(
        program_source(program),
        allowed_tools=set(state._allowed_tools or ()),
        allowed_parameters=set(args),
        program_name=program_name,
    )
    if not validation.ok:
        return (
            {
                "ok": False,
                "failed_phase": "program_validation",
                "reason": "; ".join(validation.findings),
                "trace": [],
            },
            state.env.take_snapshot(),
        )

    trace = []
    last_observation = state.env.take_snapshot()
    aggregate: dict[str, Any] = {}
    for index, step in enumerate(validation.program, 1):
        tool = str(step["tool"])
        call_args = _expand(step["args"], args)
        result, last_observation = _dispatch_tool(state, tool, call_args)
        trace.append(
            {
                "step": index,
                "tool": tool,
                "args": call_args,
                "result": result,
            }
        )
        if isinstance(result, dict):
            for field in ("grasped", "holding", "placed", "released"):
                if field in result:
                    aggregate[field] = result[field]
        if _failed(tool, result):
            reason = (
                str(result.get("reason") or f"{tool} failed")
                if isinstance(result, dict)
                else f"{tool} returned a non-object result"
            )
            return (
                {
                    "ok": False,
                    "failed_phase": f"program-step-{index}",
                    "reason": reason,
                    "trace": trace,
                    **aggregate,
                },
                last_observation,
            )
    return (
        {
            "ok": True,
            "reason": "validated code-backed program completed",
            "trace": trace,
            **aggregate,
        },
        last_observation,
    )
