"""Capability boundary for Agent-authored skill policies.

Generated policies can combine released tools through ``_dispatch_tool``.
They cannot inspect the runtime state, choose tools dynamically, or reach
filesystem, process, network, reflection, and simulator-internal APIs.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Collection


_ALLOWED_MODULE_IMPORTS = {
    "math",
    "statistics",
}
_ALLOWED_FROM_IMPORTS: dict[str, set[str] | None] = {
    "__future__": {"annotations"},
    "collections": {"Counter", "defaultdict", "deque"},
    "math": None,
    "statistics": None,
    "typing": None,
    "roborsi.embodied.agent_loop.rollout": {
        "_dispatch_tool",
    },
}
_BANNED_NAMES = {
    "__builtins__",
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "exit",
    "getattr",
    "globals",
    "help",
    "input",
    "iter",
    "locals",
    "memoryview",
    "open",
    "quit",
    "setattr",
    "super",
    "type",
    "vars",
}
_BANNED_ATTRIBUTES = {
    "__class__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__subclasses__",
    "_impl",
    "_raw",
    "check_success",
    "get_actor_pose",
    "get_contact_point",
    "parsed_problem",
    "raw_obs",
    "region_box",
    "scene",
}
_BANNED_CONTROL_NODES = (
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.Global,
    ast.Lambda,
    ast.Nonlocal,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)
_CANDIDATE_DENIED_TOOLS = {
    "exec_python",
    "register_skill",
}
_SKILL_TEXT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcheck_success\b",
        r"\bcheck_task_success\b",
        r"\bstate\.env\b",
        r"\b(?:state\.)?_impl\b",
        r"\braw_obs\b",
        r"\bparsed_problem\b",
        r"\bregion_box\b",
        r"\bget_actor_pose\b",
        r"\bget_contact_point\b",
        r"\bground[- ]truth\b",
        r"真值",
        r"\benvs/[a-z0-9_/-]+\.py\b",
    )
]


@dataclass(frozen=True)
class SafetyFinding:
    line: int
    code: str
    detail: str

    def to_dict(self) -> dict[str, int | str]:
        return {"line": self.line, "code": self.code, "detail": self.detail}


class UnsafeProposalError(ValueError):
    def __init__(self, findings: list[SafetyFinding]) -> None:
        self.findings = findings
        text = "; ".join(
            f"L{item.line} {item.code}: {item.detail}"
            for item in findings[:8]
        )
        super().__init__(f"unsafe Agent-authored policy: {text}")


def public_tool_names(namespace: str) -> set[str]:
    """Return the callable tools exposed to an Engineer for one embodiment."""
    from roborsi.embodied.agent_loop.prompt_tools import (
        _hidden_tools,
        _legacy_tool_names,
    )
    from roborsi.embodied.skills import discover_ns

    hidden = _hidden_tools(namespace)
    legacy = _legacy_tool_names(namespace)
    return {
        skill.name
        for skill in discover_ns(namespace)
        if skill.name not in hidden
        and skill.name not in _CANDIDATE_DENIED_TOOLS
        and (
            skill.name in legacy
            or _declares_dispatch_runtime(skill.path.parent / "policy.py")
        )
    }


def assert_safe_candidate(
    code: str,
    *,
    namespace: str,
    candidate_name: str | None = None,
) -> None:
    findings = inspect_candidate(
        code,
        allowed_tools=public_tool_names(namespace),
        candidate_name=candidate_name,
    )
    if findings:
        raise UnsafeProposalError(findings)


def assert_safe_skill_text(text: str) -> None:
    findings = inspect_skill_text(text)
    if findings:
        raise UnsafeProposalError(findings)


def inspect_skill_text(text: str) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for pattern in _SKILL_TEXT_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(SafetyFinding(
                    line=line_no,
                    code="skill_text",
                    detail=f"skill text contains privileged marker '{match.group(0)}'",
                ))
    return _dedupe(findings)


def inspect_candidate(
    code: str,
    *,
    allowed_tools: Collection[str],
    candidate_name: str | None = None,
) -> list[SafetyFinding]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [
            SafetyFinding(
                line=int(exc.lineno or 1),
                code="syntax_error",
                detail=str(exc.msg),
            )
        ]

    findings: list[SafetyFinding] = []
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    allowed_tool_set = set(allowed_tools)
    dispatch_defs = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "dispatch_runtime"
    ]
    if len(dispatch_defs) != 1:
        findings.append(SafetyFinding(
            line=1,
            code="entrypoint",
            detail="candidate must define exactly one dispatch_runtime(state, args)",
        ))
    else:
        findings.extend(_inspect_entrypoint(dispatch_defs[0]))

    imported_dispatch = False
    dispatch_calls = 0
    for statement in tree.body:
        if not _allowed_module_statement(statement):
            findings.append(SafetyFinding(
                int(getattr(statement, "lineno", 1) or 1),
                "module_scope",
                "module scope may contain only imports, constants, and functions",
            ))

    for node in ast.walk(tree):
        line = int(getattr(node, "lineno", 1) or 1)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name not in _ALLOWED_MODULE_IMPORTS
                    or alias.asname is not None
                ):
                    findings.append(SafetyFinding(
                        line, "import", f"module '{alias.name}' is not allowed"
                    ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            allowed_names = _ALLOWED_FROM_IMPORTS.get(module)
            names = {alias.name for alias in node.names}
            aliases = {alias.asname for alias in node.names if alias.asname}
            if (
                module not in _ALLOWED_FROM_IMPORTS
                or (allowed_names is not None and not names <= allowed_names)
                or aliases
                or "*" in names
                or any(name.startswith("_") and name != "_dispatch_tool" for name in names)
            ):
                findings.append(SafetyFinding(
                    line,
                    "import",
                    f"from '{module}' import {sorted(names)} is not allowed",
                ))
            if module == "roborsi.embodied.agent_loop.rollout" and names == {
                "_dispatch_tool"
            } and not aliases:
                imported_dispatch = True
        elif isinstance(node, ast.Name):
            if node.id in _BANNED_NAMES or (
                node.id.startswith("__") and node.id.endswith("__")
            ):
                findings.append(SafetyFinding(
                    line, "reflection", f"name '{node.id}' is forbidden"
                ))
            if node.id == "state" and not _is_direct_dispatch_state(node, parents):
                findings.append(SafetyFinding(
                    line,
                    "capability",
                    "state may only be the first argument to _dispatch_tool",
                ))
            if node.id == "_dispatch_tool" and not _is_direct_call_target(node, parents):
                findings.append(SafetyFinding(
                    line,
                    "dispatcher_alias",
                    "_dispatch_tool may only be called directly",
                ))
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in _BANNED_ATTRIBUTES:
                findings.append(SafetyFinding(
                    line, "attribute", f"attribute '{node.attr}' is forbidden"
                ))
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            findings.append(SafetyFinding(
                line,
                "dynamic_structure",
                f"decorators are not allowed on function '{node.name}'",
            ))
        elif isinstance(node, _BANNED_CONTROL_NODES):
            findings.append(SafetyFinding(
                line,
                "dynamic_structure",
                f"{type(node).__name__} is not allowed in generated policy code",
            ))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _BANNED_NAMES:
                findings.append(SafetyFinding(
                    line, "reflection", f"call '{name}' is forbidden"
                ))
            if isinstance(node.func, ast.Name) and node.func.id == "_dispatch_tool":
                dispatch_calls += 1
                findings.extend(_inspect_dispatch_call(
                    node,
                    allowed_tools=allowed_tool_set,
                    candidate_name=candidate_name,
                ))

    if not imported_dispatch:
        findings.append(SafetyFinding(
            1,
            "dispatcher_import",
            "candidate must import only _dispatch_tool from rollout",
        ))
    if dispatch_calls == 0:
        findings.append(SafetyFinding(
            1,
            "dispatcher_call",
            "candidate must call at least one released tool via _dispatch_tool",
        ))

    return _dedupe(findings)


def _inspect_entrypoint(node: ast.FunctionDef) -> list[SafetyFinding]:
    positional = [*node.args.posonlyargs, *node.args.args]
    names = [arg.arg for arg in positional]
    findings: list[SafetyFinding] = []
    if names != ["state", "args"] or node.args.vararg or node.args.kwarg:
        findings.append(SafetyFinding(
            int(node.lineno),
            "entrypoint",
            "dispatch_runtime must have exactly the positional parameters state, args",
        ))
    if node.decorator_list:
        findings.append(SafetyFinding(
            int(node.lineno),
            "entrypoint",
            "dispatch_runtime decorators are not allowed",
        ))
    return findings


def _declares_dispatch_runtime(path) -> bool:
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "dispatch_runtime"
        for node in tree.body
    )


def _inspect_dispatch_call(
    node: ast.Call,
    *,
    allowed_tools: set[str],
    candidate_name: str | None,
) -> list[SafetyFinding]:
    line = int(node.lineno)
    findings: list[SafetyFinding] = []
    if len(node.args) < 2:
        return [SafetyFinding(
            line,
            "dispatcher_call",
            "_dispatch_tool requires state and a literal public tool name",
        )]
    first, second = node.args[:2]
    if not isinstance(first, ast.Name) or first.id != "state":
        findings.append(SafetyFinding(
            line,
            "dispatcher_call",
            "_dispatch_tool must receive state directly as its first argument",
        ))
    if not isinstance(second, ast.Constant) or not isinstance(second.value, str):
        findings.append(SafetyFinding(
            line,
            "dynamic_tool",
            "_dispatch_tool requires a literal tool name",
        ))
        return findings
    tool_name = second.value
    if tool_name == candidate_name:
        findings.append(SafetyFinding(
            line,
            "recursive_tool",
            f"candidate cannot dispatch itself ('{tool_name}')",
        ))
    elif tool_name not in allowed_tools:
        findings.append(SafetyFinding(
            line,
            "nonpublic_tool",
            f"tool '{tool_name}' is not public in this embodiment",
        ))
    return findings


def _is_direct_dispatch_state(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    parent = parents.get(node)
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "_dispatch_tool"
        and bool(parent.args)
        and parent.args[0] is node
    )


def _is_direct_call_target(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    parent = parents.get(node)
    return isinstance(parent, ast.Call) and parent.func is node


def _allowed_module_statement(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
        return True
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(
            node.value.value, str
        )
    if isinstance(node, ast.Assign):
        return _static_value(node.value)
    if isinstance(node, ast.AnnAssign):
        return node.value is None or _static_value(node.value)
    return False


def _static_value(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return all(_static_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _static_value(key) and _static_value(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.UnaryOp):
        return _static_value(node.operand)
    if isinstance(node, ast.BinOp):
        return _static_value(node.left) and _static_value(node.right)
    return False


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dedupe(findings: list[SafetyFinding]) -> list[SafetyFinding]:
    seen: set[tuple[int, str, str]] = set()
    out = []
    for item in findings:
        key = (item.line, item.code, item.detail)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
