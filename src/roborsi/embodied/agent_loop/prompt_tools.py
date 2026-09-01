"""LIBERO skill discovery, tool schemas, and adaptive proposal tools."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop.config import _embodiment_line, _rules_for

_PLUGIN_CACHE: dict[tuple[str, str], Any] = {}
_COMPOUND_CACHE: dict[tuple[str, str], Any] = {}
_HIDDEN = {
    "check_success",
    "check_task_success",
    "describe_scene",
    "get_object_pose",
    "read_object_pose",
}
_META = {
    "read_skill_code",
    "list_base_skills",
    "propose_new_skill",
    "propose_skill_update",
}


def _hidden_tools(ns: str = "libero") -> set[str]:
    if ns != "libero":
        raise ValueError(f"unsupported public skill namespace: {ns}")
    return set(_HIDDEN)


def _load_dispatch_runtime(path: Path, module_name: str):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "dispatch_runtime", None)


def _try_load_plugin_dispatcher(name: str, ns: str = "libero"):
    if ns != "libero":
        return None
    key = (ns, name)
    if key not in _PLUGIN_CACHE:
        from roborsi.embodied.skills import get_ns

        skill = get_ns(name, ns)
        _PLUGIN_CACHE[key] = (
            None
            if skill is None
            else _load_dispatch_runtime(skill.path.parent / "policy.py", f"_libero_{name}")
        )
    return _PLUGIN_CACHE[key]


def _try_load_compound_dispatcher(name: str, task: str):
    key = (task, name)
    if key not in _COMPOUND_CACHE:
        from roborsi.embodied.skills import discover_compounds

        skill = next(
            (candidate for candidate in discover_compounds(task) if candidate.name == name),
            None,
        )
        _COMPOUND_CACHE[key] = (
            None
            if skill is None
            else _load_dispatch_runtime(
                skill.path.parent / "policy.py",
                f"_libero_compound_{task}_{name}",
            )
        )
    return _COMPOUND_CACHE[key]


def _description(skill: Any) -> str:
    frontmatter = skill.frontmatter or {}
    parts = [str(frontmatter.get("description") or skill.description or "").strip()]
    if isinstance(frontmatter.get("when_to_use"), str):
        parts.append("When to use: " + frontmatter["when_to_use"].strip())
    if isinstance(frontmatter.get("when_NOT_to_use"), str):
        parts.append("When not to use: " + frontmatter["when_NOT_to_use"].strip())
    return "\n\n".join(part for part in parts if part)


def _tool_spec(skill: Any) -> dict[str, Any]:
    frontmatter = skill.frontmatter or {}
    type_map = {
        "int": "integer",
        "float": "number",
        "list": "array",
        "object": "object",
        "bool": "boolean",
        "string": "string",
    }
    properties: dict[str, Any] = {}
    required: list[str] = []
    arguments = frontmatter.get("args") or frontmatter.get("params") or {}
    if isinstance(arguments, dict):
        for name, metadata in arguments.items():
            if not isinstance(metadata, dict) or name in {"env", "workdir", "model"}:
                continue
            field: dict[str, Any] = {
                "type": type_map.get(str(metadata.get("type") or "string"), "string")
            }
            if metadata.get("description"):
                field["description"] = str(metadata["description"])
            if "default" in metadata:
                field["default"] = metadata["default"]
            if "enum" in metadata:
                field["enum"] = metadata["enum"]
            if field["type"] == "array":
                field["items"] = {}
            properties[str(name)] = field
            if metadata.get("required"):
                required.append(str(name))
    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": _description(skill),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _compound_specs(task: str) -> list[dict[str, Any]]:
    if (
        not task
        or os.environ.get("ROBORSI_ATOMIC_COMPOUND", "1") != "1"
        or os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") != "0"
    ):
        return []
    from roborsi.embodied.skills import discover_compounds

    return [
        _tool_spec(skill)
        for skill in discover_compounds(task)
        if _try_load_compound_dispatcher(skill.name, task) is not None
    ]


def _build_tool_specs(ns: str = "libero", task: str = "") -> list[dict[str, Any]]:
    if ns != "libero":
        raise ValueError(f"unsupported public skill namespace: {ns}")
    from roborsi.embodied.skills import discover_ns

    specs = [
        _tool_spec(skill)
        for skill in discover_ns("libero")
        if skill.name not in _HIDDEN
        and _try_load_plugin_dispatcher(skill.name, "libero") is not None
    ]
    specs.sort(key=lambda value: value["function"]["name"])
    specs.extend(_compound_specs(task))
    specs.append(
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "End the episode with a visible completion assessment.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["success"],
                },
            },
        }
    )
    if os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") == "0":
        specs.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read_skill_code",
                        "description": "Read an existing visible LIBERO skill implementation.",
                        "parameters": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_base_skills",
                        "description": "List registered visible LIBERO base skills.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "propose_new_skill",
                        "description": "Queue a complete new LIBERO skill for harness review.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "code": {"type": "string"},
                                "skill_md": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": ["name", "description", "code", "skill_md", "rationale"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "propose_skill_update",
                        "description": "Queue a complete replacement for an existing LIBERO skill.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "new_code": {"type": "string"},
                                "skill_md": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": ["name", "new_code", "rationale"],
                        },
                    },
                },
            ]
        )
    return specs


def _system_prompt(ns: str = "libero") -> str:
    prompt = _embodiment_line(ns) + "\n\n" + _rules_for(ns)
    if os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") == "0":
        prompt += (
            "\nADAPTATION: after two materially different strategies fail, inspect the "
            "closest skill and queue a complete camera/proprioception-only proposal. "
            "A proposal cannot change the current episode and requires a later harness gate."
        )
    return prompt


def _build_status_check_prompt() -> str:
    return (
        "Inspect the latest visible result. Proceed with a materially new action, "
        "re-localize if the scene changed, or call done with an honest visible verdict."
    )


def _proposal_root() -> Path:
    configured = os.environ.get("ROBORSI_PROPOSAL_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / ".roborsi/proposals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _queue_proposal(kind: str, payload: dict[str, Any], task: str) -> str:
    proposal_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{kind}-{uuid.uuid4().hex[:8]}"
    )
    record = {
        "schema": "roborsi.libero_skill_proposal.v1",
        "id": proposal_id,
        "kind": kind,
        "task": task,
        "benchmark_task": os.environ.get("ROBORSI_TASK_KEY", ""),
        "status": "pending",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    path = _proposal_root() / f"{proposal_id}.json"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, ensure_ascii=True, sort_keys=True)
        stream.write("\n")
    return proposal_id


def _dispatch_meta_tool(
    name: str,
    args: dict[str, Any],
    *,
    ns: str = "libero",
    task: str = "",
) -> dict[str, Any] | None:
    if name not in _META:
        return None
    if ns != "libero":
        return {"ok": False, "reason": "unsupported namespace"}
    if os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") != "0":
        return {"ok": False, "reason": "adaptive tools are disabled in fixed evaluation"}
    from roborsi.embodied.skills import discover_ns, get_ns

    if name == "list_base_skills":
        rows = [
            {"name": skill.name, "description": skill.description[:160]}
            for skill in discover_ns("libero")
            if skill.name not in _HIDDEN
        ]
        return {"ok": True, "count": len(rows), "skills": rows}
    if name == "read_skill_code":
        requested = str(args.get("name") or "")
        skill = None if requested in _HIDDEN else get_ns(requested, "libero")
        if skill is None:
            return {"ok": False, "reason": f"unknown visible LIBERO skill: {requested}"}
        policy = skill.path.parent / "policy.py"
        return {
            "ok": True,
            "name": requested,
            "policy_py": policy.read_text(encoding="utf-8")[:12000] if policy.is_file() else "",
        }
    if name == "propose_new_skill":
        proposal_id = _queue_proposal("new", dict(args), task)
        return {"ok": True, "proposal_id": proposal_id, "applied": False}
    requested = str(args.get("name") or "")
    if requested in _HIDDEN or get_ns(requested, "libero") is None:
        return {"ok": False, "reason": f"unknown visible LIBERO skill: {requested}"}
    proposal_id = _queue_proposal("update", dict(args), task)
    return {"ok": True, "proposal_id": proposal_id, "applied": False}
