"""Fail-closed adaptive skill overlay and simulator promotion gate."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FORBIDDEN_ATTRS = {
    "__dict__",
    "_env",
    "_raw",
    "check_success",
    "goal_state",
    "parsed_problem",
    "region_box",
}
_FORBIDDEN_TEXT = {
    "_raw",
    "check_task",
    "check_success",
    "describe_scene",
    "get_object_pose",
    "object_pose",
    "raw_obs",
    "sim_ground_truth",
    "simulator object pose",
    "hidden object state",
}
_FORBIDDEN_IMPORT_ROOTS = {"libero", "mujoco", "robosuite"}


@dataclass(frozen=True)
class ProposalValidation:
    ok: bool
    findings: tuple[str, ...]


def _proposal_code(proposal: dict[str, Any]) -> str:
    payload = proposal.get("payload") or {}
    if proposal.get("kind") == "new":
        return str(payload.get("code") or "")
    return str(payload.get("new_code") or "")


def validate_proposal(proposal: dict[str, Any]) -> ProposalValidation:
    findings: list[str] = []
    kind = str(proposal.get("kind") or "")
    payload = proposal.get("payload") or {}
    if kind not in {"new", "update"}:
        findings.append("kind must be new or update")
    name = str(payload.get("name") or "")
    if not _NAME.fullmatch(name):
        findings.append("skill name must be a lowercase identifier")
    code = _proposal_code(proposal)
    if not code.strip():
        findings.append("proposal code is empty")
        return ProposalValidation(False, tuple(findings))
    try:
        tree = ast.parse(code, filename=f"proposal:{name}")
    except SyntaxError as exc:
        findings.append(f"syntax error: {exc.msg} at line {exc.lineno}")
        return ProposalValidation(False, tuple(findings))
    lowered_code = code.lower()
    for text in _FORBIDDEN_TEXT:
        if text in lowered_code:
            findings.append(f"forbidden hidden-state reference: {text}")
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "dispatch_runtime"
        for node in tree.body
    ):
        findings.append("proposal must define dispatch_runtime")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            findings.append(f"forbidden hidden-state attribute: {node.attr}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _FORBIDDEN_IMPORT_ROOTS:
                    findings.append(f"forbidden direct simulator import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module.split(".", 1)[0] in _FORBIDDEN_IMPORT_ROOTS:
                findings.append(f"forbidden direct simulator import: {module}")
            if module.startswith("roborsi.embodied.sim"):
                findings.append(f"forbidden direct simulator import: {module}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for text in _FORBIDDEN_TEXT:
                if text in lowered:
                    findings.append(f"forbidden hidden-state reference: {text}")
    if kind == "new":
        skill_md = str(payload.get("skill_md") or "")
        if not skill_md.startswith("---"):
            findings.append("new skill requires complete SKILL.md frontmatter")
    return ProposalValidation(not findings, tuple(sorted(set(findings))))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _stage_proposal(campaign_root: Path, proposal: dict[str, Any]) -> tuple[Path, Path]:
    from roborsi.embodied.skills import get_ns

    proposal_id = str(proposal["id"])
    payload = proposal["payload"]
    name = str(payload["name"])
    overlay = campaign_root / "candidate_overlays" / proposal_id
    skill_dir = overlay / "embodied/skills/base" / name / "libero"
    skill_dir.mkdir(parents=True, exist_ok=True)
    if proposal["kind"] == "update":
        source = get_ns(name, "libero")
        if source is None:
            raise ValueError(f"cannot update unknown LIBERO skill: {name}")
        source_md = source.path
        target_md = skill_dir / "SKILL.md"
        if not target_md.exists():
            shutil.copy2(source_md, target_md)
        code = str(payload.get("new_code") or "")
    else:
        target_md = skill_dir / "SKILL.md"
        skill_md = str(payload.get("skill_md") or "")
        if not target_md.exists():
            target_md.write_text(skill_md, encoding="utf-8")
        code = str(payload.get("code") or "")
    policy = skill_dir / "policy.py"
    if policy.exists() and policy.read_text(encoding="utf-8") != code:
        raise ValueError(f"candidate overlay already exists with different code: {policy}")
    if not policy.exists():
        policy.write_text(code, encoding="utf-8")
    return overlay, skill_dir


def _default_harness(
    *,
    campaign_root: Path,
    proposal: dict[str, Any],
    candidate_root: Path,
    seed: int,
    release_id: str,
) -> dict[str, Any]:
    from roborsi.embodied.agent_loop.prompt_tools import _COMPOUND_CACHE, _PLUGIN_CACHE
    from roborsi.embodied.sim.libero.run_records import load_records
    from roborsi_libero.config import load_config
    from roborsi_libero.worker import run_assigned_tasks

    task = str(proposal.get("benchmark_task") or "")
    if not task:
        return {
            "success": False,
            "category": "implementation_failure",
            "simulator_verdict": None,
            "detail": "proposal has no benchmark_task",
        }
    previous_workspace = os.environ.get("ROBORSI_WORKSPACE")
    _PLUGIN_CACHE.clear()
    _COMPOUND_CACHE.clear()
    config = load_config(campaign_root / "config.resolved.yaml")
    try:
        journal = run_assigned_tasks(
            config,
            campaign_root=campaign_root,
            seed=seed,
            release_id=release_id,
            worker=0,
            task_keys=[task],
            workspace_root=candidate_root,
            allow_changed_path=True,
            journal_tag=f"harness-{proposal['id']}",
        )
    finally:
        if previous_workspace is None:
            os.environ.pop("ROBORSI_WORKSPACE", None)
        else:
            os.environ["ROBORSI_WORKSPACE"] = previous_workspace
        _PLUGIN_CACHE.clear()
        _COMPOUND_CACHE.clear()
    candidates = [
        row
        for row in load_records(journal)
        if row.identity.task_key == task
        and int(row.identity.seed) == seed
        and row.code_fingerprint == f"release:{release_id}"
    ]
    if not candidates:
        return {
            "success": False,
            "category": "implementation_failure",
            "simulator_verdict": None,
            "detail": "candidate harness produced no terminal record",
        }
    row = max(candidates, key=lambda value: int(value.identity.attempt))
    return {
        "success": row.category == "task_success",
        "category": row.category,
        "simulator_verdict": (
            row.category if row.category in {"task_success", "task_failure"} else None
        ),
        "task_key": task,
        "seed": seed,
        "attempt": row.identity.attempt,
        "journal": journal.name,
        "video": Path(str(row.video_path or "")).name,
        "trajectory": Path(str(row.trajectory_path or "")).name,
    }


def _promote(
    campaign_root: Path,
    *,
    release_id: str,
    candidate_skill_dir: Path,
    skill_name: str,
) -> None:
    relative = Path("embodied/skills/base") / skill_name / "libero"
    immutable = campaign_root / "releases" / release_id / relative
    promoted = campaign_root / "workspace" / relative
    immutable.parent.mkdir(parents=True, exist_ok=True)
    promoted.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate_skill_dir, immutable, dirs_exist_ok=False)
    shutil.copytree(candidate_skill_dir, promoted, dirs_exist_ok=True)


def process_pending_proposals(
    campaign_root: Path | str,
    *,
    seed: int,
    run_harness: Callable[..., dict[str, Any]] | None = None,
    max_proposals: int = 4,
) -> str | None:
    root = Path(campaign_root).resolve()
    harness = run_harness or _default_harness
    latest_release: str | None = None
    processed = 0
    previous_workspace = os.environ.get("ROBORSI_WORKSPACE")
    for path in sorted((root / "proposals").glob("*.json")):
        proposal = json.loads(path.read_text(encoding="utf-8"))
        if proposal.get("status") != "pending":
            continue
        if processed >= max_proposals:
            break
        processed += 1
        validation = validate_proposal(proposal)
        proposal["static_validation"] = {
            "ok": validation.ok,
            "findings": list(validation.findings),
        }
        if not validation.ok:
            proposal["status"] = "rejected_static"
            _write_json(path, proposal)
            continue
        os.environ["ROBORSI_WORKSPACE"] = str(root / "workspace")
        try:
            candidate_root, skill_dir = _stage_proposal(root, proposal)
        except Exception as exc:  # noqa: BLE001
            proposal["status"] = "rejected_static"
            proposal["static_validation"]["ok"] = False
            proposal["static_validation"]["findings"].append(
                f"staging failed: {type(exc).__name__}: {exc}"
            )
            _write_json(path, proposal)
            continue
        release_id = f"adaptive-seed{seed}-{proposal['id']}"
        result = harness(
            campaign_root=root,
            proposal=proposal,
            candidate_root=candidate_root,
            seed=seed,
            release_id=release_id,
        )
        proposal["harness_result"] = result
        passed = bool(
            result.get("success") is True
            and result.get("category") == "task_success"
            and result.get("simulator_verdict") == "task_success"
        )
        if not passed:
            proposal["status"] = "rejected_harness"
            _write_json(path, proposal)
            continue
        _promote(
            root,
            release_id=release_id,
            candidate_skill_dir=skill_dir,
            skill_name=str(proposal["payload"]["name"]),
        )
        proposal["status"] = "applied"
        proposal["release_id"] = release_id
        _write_json(path, proposal)
        latest_release = release_id
    if previous_workspace is None:
        os.environ.pop("ROBORSI_WORKSPACE", None)
    else:
        os.environ["ROBORSI_WORKSPACE"] = previous_workspace
    return latest_release
