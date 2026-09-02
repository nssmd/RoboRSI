"""Discover shipped skills and the active campaign's skill overlay."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHIPPED_ROOT = Path(__file__).resolve().parent


def user_root() -> Path:
    from roborsi.embodied.paths import workspace_skills_root

    return workspace_skills_root()


def _roots() -> tuple[tuple[Path, bool], ...]:
    import os

    if os.environ.get("ROBORSI_WORKSPACE"):
        return ((user_root(), True), (SHIPPED_ROOT, False))
    return ((SHIPPED_ROOT, False),)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    category: str  # e.g. "perception", "manipulation", "setup"
    path: Path  # path to SKILL.md
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    is_user: bool = False  # True if from ~/.roborsi (user), False if shipped

    @property
    def backends(self) -> tuple[str, ...]:
        metadata = self.frontmatter.get("metadata") or {}
        values = metadata.get("backends") if isinstance(metadata, dict) else None
        if isinstance(values, list):
            return tuple(str(value) for value in values)
        robot = self.frontmatter.get("robot")
        return (str(robot),) if robot else ()

    @property
    def namespace(self) -> str:
        robot = self.frontmatter.get("robot")
        if robot:
            return str(robot)
        return self.backends[0] if self.backends else ""

    @property
    def reference(self) -> str:
        if self.category == "base" and self.namespace:
            return f"{self.namespace}/{self.name}"
        return self.name


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a skill document."""
    if not content.startswith("---"):
        return {}, content
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}, content
    yaml_text = content[3 : end.start() + 3]
    body = content[end.end() + 3 :]
    import yaml

    parsed = yaml.safe_load(yaml_text)
    return (parsed if isinstance(parsed, dict) else {}), body


def _discover_root(root: Path, is_user: bool) -> list[Skill]:
    skills: list[Skill] = []
    if not root.exists():
        return skills
    for skill_md in root.rglob("SKILL.md"):
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = parse_frontmatter(content)
        skill_dir = skill_md.parent
        name = str(fm.get("name") or skill_dir.name)
        description = str(fm.get("description") or _first_nonheader_line(body))
        # category = first segment of path relative to root, or skill_dir.parent.name
        try:
            rel = skill_md.relative_to(root)
            category = rel.parts[0] if len(rel.parts) >= 3 else ""
        except ValueError:
            category = ""
        if not category:
            category = skill_dir.parent.name if skill_dir.parent != root else ""
        skills.append(
            Skill(
                name=name,
                description=description,
                category=category,
                path=skill_md,
                frontmatter=fm,
                body=body,
                is_user=is_user,
            )
        )
    return skills


def _first_nonheader_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:200]
    return ""


def discover() -> list[Skill]:
    """Return the shipped skills plus an explicit campaign overlay, if set."""
    out: list[Skill] = []
    seen: set[tuple[str, str, str]] = set()
    for root, is_user in _roots():
        for sk in _discover_root(root, is_user):
            identity = (sk.category, sk.namespace, sk.name)
            if identity in seen:
                continue
            seen.add(identity)
            out.append(sk)
    return out


def find(
    name: str,
    *,
    backend: str | None = None,
    category: str | None = None,
) -> list[Skill]:
    """Return every skill matching a local or ``backend/name`` reference."""
    local_name = name
    selected_backend = backend
    if "/" in name:
        prefix, candidate = name.split("/", 1)
        if prefix and candidate:
            selected_backend = selected_backend or prefix
            local_name = candidate
    elif ":" in name:
        prefix, candidate = name.split(":", 1)
        if prefix and candidate:
            selected_backend = selected_backend or prefix
            local_name = candidate

    matches = []
    for skill in discover():
        if skill.name != local_name:
            continue
        if category is not None and skill.category != category:
            continue
        if selected_backend is not None and (
            selected_backend != skill.namespace and selected_backend not in skill.backends
        ):
            continue
        matches.append(skill)
    return matches


def get(
    name: str,
    *,
    backend: str | None = None,
    category: str | None = None,
) -> Skill | None:
    """Resolve one unambiguous skill reference."""
    matches = find(name, backend=backend, category=category)
    return matches[0] if len(matches) == 1 else None


def discover_ns(ns: str) -> list[Skill]:
    """Discover Base Skills for one explicit backend namespace."""
    return [
        skill
        for skill in discover()
        if skill.category == "base" and (skill.namespace == ns or ns in skill.backends)
    ]


def get_ns(name: str, ns: str) -> Skill | None:
    """Resolve a base skill by (name, namespace) — the namespace-scoped
    counterpart of ``get`` used by the dispatch/prompt layer."""
    return get(name, backend=ns, category="base")


def discover_atomic(backend: str | None = None) -> list[Skill]:
    """Return Atomic Skills, optionally restricted to one backend."""
    return [
        skill
        for skill in discover()
        if skill.category == "atomic"
        and (backend is None or skill.namespace == backend or backend in skill.backends)
    ]


def get_atomic_by_task_key(task_key: str, *, backend: str) -> Skill | None:
    """Resolve one Atomic Skill from its public benchmark task key."""
    matches = []
    for skill in discover_atomic(backend):
        metadata = skill.frontmatter.get("metadata") or {}
        benchmark = metadata.get("benchmark") if isinstance(metadata, dict) else None
        if isinstance(benchmark, dict) and str(benchmark.get("task_key") or "") == task_key:
            matches.append(skill)
    return matches[0] if len(matches) == 1 else None


def discover_executors(task_family: str, *, backend: str) -> list[Skill]:
    """Return executor profiles attached to a Task Family."""
    return [
        skill
        for skill in discover()
        if skill.category == "executors"
        and skill.frontmatter.get("parent") == task_family
        and (skill.namespace == backend or backend in skill.backends)
    ]


def discover_compounds(task: str) -> list[Skill]:
    """Return code-backed compounds published for one task family."""
    out: list[Skill] = []
    seen: set[str] = set()
    for root, is_user in _roots():
        for sk in _discover_root(root, is_user):
            frontmatter = sk.frontmatter or {}
            metadata = frontmatter.get("metadata") or {}
            if frontmatter.get("parent") != task:
                continue
            if not isinstance(metadata, dict) or metadata.get("compound") is not True:
                continue
            if not (sk.path.parent / "policy.py").exists() or sk.name in seen:
                continue
            seen.add(sk.name)
            out.append(sk)
    return out
