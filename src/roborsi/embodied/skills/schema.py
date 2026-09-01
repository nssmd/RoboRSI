"""Validation for public, Agent-visible RoboRSI skill documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from roborsi.embodied.skills import Skill

EXPECTED_KIND = {
    "atomic": "atomic",
    "base": "base",
    "compound": "compound",
    "executors": "executor",
    "task_families": "task_family",
}
FORBIDDEN_KEYS = {
    "check_success",
    "check_task_success",
    "goal_state",
    "harness",
    "object_pose",
    "pass_criteria",
    "region_box",
    "reward",
    "sim_ground_truth",
    "sim_task",
    "success_predicate",
}
COORDINATE_KEYS = {
    "bbox",
    "coordinate",
    "coordinates",
    "object_pose",
    "pixel",
    "pose",
    "region_box",
    "source_pixel",
    "target_pixel",
    "xyz",
}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _is_numeric_sequence(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def _walk_visible_data(value: Any, prefix: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.lower()
            location = f"{prefix}.{key}" if prefix else key
            if lowered in FORBIDDEN_KEYS:
                yield f"forbidden Agent-visible key: {location}"
            if lowered in COORDINATE_KEYS and _is_numeric_sequence(child):
                yield f"fixed coordinate literal: {location}"
            yield from _walk_visible_data(child, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_visible_data(child, f"{prefix}[{index}]")


def validate_skill(skill: Skill) -> list[str]:
    fm = skill.frontmatter
    findings: list[str] = []
    expected_kind = EXPECTED_KIND.get(skill.category)

    for field in ("name", "kind", "version", "description"):
        if not fm.get(field):
            findings.append(f"missing field: {field}")
    if expected_kind is not None and fm.get("kind") != expected_kind:
        findings.append(f"kind must be {expected_kind!r}")
    if fm.get("version") and not SEMVER.fullmatch(str(fm["version"])):
        findings.append("version must use MAJOR.MINOR.PATCH")

    metadata = fm.get("metadata")
    if not isinstance(metadata, dict):
        findings.append("metadata must be a mapping")
        metadata = {}
    backends = metadata.get("backends")
    if not isinstance(backends, list) or not backends:
        findings.append("metadata.backends must be a non-empty list")
    if not metadata.get("runtime_status"):
        findings.append("metadata.runtime_status is required")

    if skill.category == "base":
        if not fm.get("robot"):
            findings.append("base.robot is required")
        if not fm.get("category"):
            findings.append("base.category is required")
        if not isinstance(fm.get("args"), dict):
            findings.append("base.args must be a mapping")
        if not isinstance(fm.get("returns"), dict):
            findings.append("base.returns must be a mapping")
    elif skill.category == "atomic":
        benchmark = metadata.get("benchmark")
        prompts = metadata.get("vlm_prompts")
        if not fm.get("parent"):
            findings.append("atomic.parent is required")
        if not isinstance(benchmark, dict) or not benchmark.get("task_key"):
            findings.append("atomic benchmark.task_key is required")
        if not isinstance(prompts, dict) or not prompts.get("instruction"):
            findings.append("atomic vlm_prompts.instruction is required")
    elif skill.category == "compound":
        if not fm.get("parent"):
            findings.append("compound.parent is required")
        if not isinstance(fm.get("args"), dict):
            findings.append("compound.args must be a mapping")
        if not isinstance(fm.get("returns"), dict):
            findings.append("compound.returns must be a mapping")
    elif skill.category == "executors":
        if not fm.get("parent") or not fm.get("phase"):
            findings.append("executor.parent and executor.phase are required")
        if not isinstance(fm.get("params"), dict):
            findings.append("executor.params must be a mapping")

    findings.extend(_walk_visible_data(fm))
    return sorted(set(findings))


def validate_catalog(skills: Iterable[Skill]) -> list[str]:
    skill_list = list(skills)
    findings: list[str] = []
    identities: set[tuple[str, str, str]] = set()
    names = {skill.name for skill in skill_list}

    for skill in skill_list:
        identity = (skill.category, skill.namespace, skill.name)
        if identity in identities:
            findings.append(f"{skill.reference}: duplicate skill identity")
        identities.add(identity)

        for finding in validate_skill(skill):
            findings.append(f"{skill.reference}: {finding}")

        parent = skill.frontmatter.get("parent")
        if parent and parent not in names:
            findings.append(f"{skill.reference}: unknown parent {parent!r}")

    return sorted(set(findings))
