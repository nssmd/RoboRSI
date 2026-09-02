"""Fail-closed adaptive skill overlay and simulator promotion gate."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_INFRASTRUCTURE = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}


@dataclass(frozen=True)
class ProposalValidation:
    ok: bool
    findings: tuple[str, ...]


def _proposal_code(proposal: dict[str, Any]) -> str:
    payload = proposal.get("payload") or {}
    return str(payload.get("program_code") or payload.get("code") or payload.get("new_code") or "")


def _allowed_program_tools(task_family: str, program_name: str) -> set[str]:
    from roborsi.embodied.skills import discover_compounds, discover_ns

    return {skill.name for skill in discover_ns("libero") if skill.name != program_name} | {
        skill.name for skill in discover_compounds(task_family) if skill.name != program_name
    }


def _validate_compound_skill_md(
    text: str,
    *,
    expected_name: str,
    expected_parent: str,
) -> list[str]:
    from roborsi.embodied.skills import parse_frontmatter

    frontmatter, _ = parse_frontmatter(text)
    findings = []
    if not frontmatter:
        return ["new skill requires complete SKILL.md frontmatter"]
    if frontmatter.get("name") != expected_name:
        findings.append("SKILL.md name must match the proposal name")
    if frontmatter.get("kind") != "compound":
        findings.append("adaptive skills must use kind: compound")
    if frontmatter.get("parent") != expected_parent:
        findings.append("SKILL.md parent must match the active Task Family")
    if not isinstance(frontmatter.get("args"), dict):
        findings.append("SKILL.md args must be a mapping")
    if not isinstance(frontmatter.get("returns"), dict):
        findings.append("SKILL.md returns must be a mapping")
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        findings.append("SKILL.md metadata must be a mapping")
    else:
        if metadata.get("compound") is not True:
            findings.append("SKILL.md metadata.compound must be true")
        if "libero" not in (metadata.get("backends") or []):
            findings.append("SKILL.md metadata.backends must include libero")
    return findings


def _proposal_parameters(proposal: dict[str, Any]) -> set[str]:
    from roborsi.embodied.skills import discover_compounds, parse_frontmatter

    payload = proposal.get("payload") or {}
    if proposal.get("kind") == "new":
        frontmatter, _ = parse_frontmatter(str(payload.get("skill_md") or ""))
    else:
        name = str(payload.get("name") or "")
        skill = next(
            (
                candidate
                for candidate in discover_compounds(str(proposal.get("task") or ""))
                if candidate.name == name
            ),
            None,
        )
        frontmatter = skill.frontmatter if skill is not None else {}
    arguments = frontmatter.get("args") if isinstance(frontmatter, dict) else {}
    return {str(name) for name in arguments} if isinstance(arguments, dict) else set()


def validate_proposal(proposal: dict[str, Any]) -> ProposalValidation:
    findings: list[str] = []
    kind = str(proposal.get("kind") or "")
    payload = proposal.get("payload") or {}
    if kind not in {"new", "update"}:
        findings.append("kind must be new or update")
    name = str(payload.get("name") or "")
    if not _NAME.fullmatch(name):
        findings.append("skill name must be a lowercase identifier")
    source = _proposal_code(proposal)
    if not source.strip():
        findings.append("proposal program is empty")
        return ProposalValidation(False, tuple(findings))
    from roborsi.libero.programs import validate_program_source

    task_family = str(proposal.get("task") or "")
    program_validation = validate_program_source(
        source,
        allowed_tools=_allowed_program_tools(task_family, name),
        allowed_parameters=_proposal_parameters(proposal),
        program_name=name,
    )
    findings.extend(program_validation.findings)
    if kind == "new":
        skill_md = str(payload.get("skill_md") or "")
        findings.extend(
            _validate_compound_skill_md(
                skill_md,
                expected_name=name,
                expected_parent=task_family,
            )
        )
    else:
        from roborsi.embodied.skills import discover_compounds

        if not any(skill.name == name for skill in discover_compounds(task_family)):
            findings.append("updates are restricted to existing Compound Skills")
    return ProposalValidation(not findings, tuple(sorted(set(findings))))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _stage_proposal(campaign_root: Path, proposal: dict[str, Any]) -> tuple[Path, Path]:
    from roborsi.embodied.skills import discover_compounds
    from roborsi.libero.programs import program_source, validate_program_source

    proposal_id = str(proposal["id"])
    payload = proposal["payload"]
    name = str(payload["name"])
    overlay = campaign_root / "candidate_overlays" / proposal_id
    current_workspace = campaign_root / "workspace"
    if current_workspace.is_dir() and not overlay.exists():
        shutil.copytree(current_workspace, overlay)
    skill_dir = overlay / "embodied/skills/compound/libero" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if proposal["kind"] == "update":
        source = next(
            (
                skill
                for skill in discover_compounds(str(proposal.get("task") or ""))
                if skill.name == name
            ),
            None,
        )
        if source is None:
            raise ValueError(f"cannot update unknown Compound Skill: {name}")
        source_md = source.path
        target_md = skill_dir / "SKILL.md"
        if not target_md.exists():
            shutil.copy2(source_md, target_md)
    else:
        target_md = skill_dir / "SKILL.md"
        skill_md = str(payload.get("skill_md") or "")
        if not target_md.exists():
            target_md.write_text(skill_md, encoding="utf-8")

    validation = validate_program_source(
        _proposal_code(proposal),
        allowed_tools=_allowed_program_tools(str(proposal.get("task") or ""), name),
        allowed_parameters=_proposal_parameters(proposal),
        program_name=name,
    )
    if not validation.ok:
        raise ValueError("; ".join(validation.findings))
    source = program_source(validation.program)
    program = skill_dir / "program.py"
    policy = skill_dir / "policy.py"
    policy_source = (
        "from __future__ import annotations\n\n"
        "from .program import PROGRAM\n"
        "from roborsi.libero.programs import execute_program\n\n"
        "def dispatch_runtime(state, args):\n"
        f"    return execute_program(PROGRAM, state, args, program_name={name!r})\n"
    )
    for path, text in ((program, source), (policy, policy_source)):
        if path.exists() and path.read_text(encoding="utf-8") != text:
            raise ValueError(f"candidate overlay already exists with different code: {path}")
        if not path.exists():
            path.write_text(text, encoding="utf-8")
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
    from roborsi.libero.config import load_config
    from roborsi.libero.worker import run_assigned_tasks

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
        and row.release_id == release_id
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
    relative = Path("embodied/skills/compound/libero") / skill_name
    immutable = campaign_root / "releases" / release_id / relative
    promoted = campaign_root / "workspace" / relative
    immutable.parent.mkdir(parents=True, exist_ok=True)
    promoted.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate_skill_dir, immutable, dirs_exist_ok=False)
    shutil.copytree(candidate_skill_dir, promoted, dirs_exist_ok=True)


def _promotion_seeds(
    campaign_root: Path,
    proposal: dict[str, Any],
    *,
    current_seed: int,
    count: int = 2,
) -> list[int]:
    from roborsi.embodied.sim.libero.run_records import load_records

    retained = proposal.get("validation_seeds")
    if isinstance(retained, list):
        selected = [int(value) for value in retained]
        if len(selected) >= count and len(selected) == len(set(selected)):
            return selected[:count]
    task = str(proposal.get("benchmark_task") or "")
    rows = [
        row
        for journal in sorted((campaign_root / "journals").glob("*.episodes.jsonl"))
        for row in load_records(journal)
        if row.identity.task_key == task
    ]
    successful = {int(row.identity.seed) for row in rows if row.category == "task_success"}
    terminal = {
        int(row.identity.seed)
        for row in rows
        if row.category in {"task_success", "task_failure", "implementation_failure"}
    }
    selected = []
    if current_seed not in successful:
        selected.append(int(current_seed))
    for seed in range(10):
        if seed in selected or seed in terminal:
            continue
        selected.append(seed)
        if len(selected) >= count:
            break
    return selected


def _passed_harness(result: dict[str, Any]) -> bool:
    return bool(
        result.get("success") is True
        and result.get("category") == "task_success"
        and result.get("simulator_verdict") == "task_success"
    )


def _harness_exception(exc: BaseException, *, seed: int) -> dict[str, Any]:
    from roborsi.embodied.sim.libero.run_records import classify_infrastructure_exception

    category = classify_infrastructure_exception(exc)
    return {
        "success": False if category == "implementation_failure" else None,
        "category": category,
        "simulator_verdict": None,
        "seed": int(seed),
        "detail": f"{type(exc).__name__}: {exc}",
    }


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
    try:
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
            release_id = str(
                proposal.get("candidate_release_id")
                or f"adaptive-seed{seed}-{proposal['id']}"
            )
            proposal["candidate_release_id"] = release_id
            validation_seeds = _promotion_seeds(
                root,
                proposal,
                current_seed=seed,
            )
            proposal["validation_seeds"] = validation_seeds
            prior_results = {
                int(result.get("seed")): result
                for result in proposal.get("harness_results") or []
                if isinstance(result, dict) and result.get("seed") is not None
            }
            results = []
            for validation_seed in validation_seeds:
                prior = prior_results.get(validation_seed)
                if prior is not None and _passed_harness(prior):
                    results.append(prior)
                    continue
                try:
                    result = harness(
                        campaign_root=root,
                        proposal=proposal,
                        candidate_root=candidate_root,
                        seed=validation_seed,
                        release_id=release_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    result = _harness_exception(exc, seed=validation_seed)
                result = dict(result)
                result.setdefault("seed", validation_seed)
                results.append(result)
            proposal["harness_result"] = results[0] if results else {}
            proposal["harness_results"] = results
            if any(str(result.get("category") or "") in _INFRASTRUCTURE for result in results):
                proposal["status"] = "pending"
                proposal["validation_status"] = "infrastructure_interrupted"
                _write_json(path, proposal)
                continue
            passed = len(results) >= 2 and all(_passed_harness(result) for result in results)
            if not passed:
                proposal["status"] = "rejected_harness"
                proposal["validation_status"] = "failed"
                _write_json(path, proposal)
                continue
            _promote(
                root,
                release_id=release_id,
                candidate_skill_dir=skill_dir,
                skill_name=str(proposal["payload"]["name"]),
            )
            proposal["status"] = "applied"
            proposal["validation_status"] = "passed"
            proposal["release_id"] = release_id
            _write_json(path, proposal)
            latest_release = release_id
    finally:
        if previous_workspace is None:
            os.environ.pop("ROBORSI_WORKSPACE", None)
        else:
            os.environ["ROBORSI_WORKSPACE"] = previous_workspace
    return latest_release
