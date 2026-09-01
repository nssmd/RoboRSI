"""Skill discovery and loading for the RoboRSI CLI.

RoboRSI skill documents follow a small, explicit convention:
  - Walk <roots>/<category>/<name>/SKILL.md
  - Parse YAML frontmatter (name, description, version, metadata, ...)
  - Expose via CLI (``roborsi skill list|show|run``) and AI tool (SkillToolGroup)

Two roots:
  1. Shipped:   ``roborsi/embodied/skills/`` (this file's directory)
  2. User:      ``~/.roborsi/workspace/embodied/skills/`` (user-authored)
Shipped skills take precedence on name collision.

Skills are *documents*, not executables — the agent reads them and then
calls lower-level tools (``flexiv``, ``camera``, ``perceive``). A skill
*may* ship a ``policy.py`` with an optional ``run()`` entry point for
scripted execution, but the markdown is canonical.
"""

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
    category: str                    # e.g. "perception", "manipulation", "setup"
    path: Path                       # path to SKILL.md
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    is_user: bool = False            # True if from ~/.roborsi (user), False if shipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "path": str(self.path),
            "is_user": self.is_user,
            "version": self.frontmatter.get("version", ""),
            "tags": self.frontmatter.get("metadata", {}).get("tags", []) if isinstance(self.frontmatter.get("metadata"), dict) else [],
        }


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter, with a line-by-line ``k:v`` fallback."""
    if not content.startswith("---"):
        return {}, content
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}, content
    yaml_text = content[3:end.start() + 3]
    body = content[end.end() + 3:]
    try:
        import yaml
        parsed = yaml.safe_load(yaml_text)
        if isinstance(parsed, dict):
            return parsed, body
    except Exception:
        pass
    # Fallback: simple k:v parsing
    fm: dict[str, Any] = {}
    for line in yaml_text.strip().splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip()
    return fm, body


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
        skills.append(Skill(
            name=name,
            description=description,
            category=category,
            path=skill_md,
            frontmatter=fm,
            body=body,
            is_user=is_user,
        ))
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
    seen: set[str] = set()
    for root, is_user in _roots():
        for sk in _discover_root(root, is_user):
            if sk.name in seen:
                continue
            seen.add(sk.name)
            out.append(sk)
    return out


def get(name: str) -> Skill | None:
    for sk in discover():
        if sk.name == name:
            return sk
    return None


def discover_ns(ns: str) -> list[Skill]:
    """Discover the explicit public ``base/libero`` namespace."""
    if ns != "libero":
        return []
    out: list[Skill] = []
    seen: set[str] = set()
    for root, is_user in _roots():
        for sk in _discover_root(root, is_user):
            parts = sk.path.parent.parts
            if "base" not in parts or ns not in parts:
                continue
            if sk.name in seen:
                continue
            seen.add(sk.name)
            out.append(sk)
    return out


def get_ns(name: str, ns: str) -> Skill | None:
    """Resolve a base skill by (name, namespace) — the namespace-scoped
    counterpart of ``get`` used by the dispatch/prompt layer."""
    for sk in discover_ns(ns):
        if sk.name == name:
            return sk
    return None


def discover_compounds(task: str) -> list[Skill]:
    """Solidified compound policies scoped to ONE atomic task:
    ``atomic/<task>/<name>/`` where ``<name> != 'zeroshot'`` and a ``policy.py``
    sits beside SKILL.md. These are Engineer-callable macros that codify a task's
    proven recipe in code (composing base skills); opt-in via
    ROBORSI_ATOMIC_COMPOUND. Scoped per-task (not deduped globally) so each
    task's Engineer only sees its own compounds."""
    out: list[Skill] = []
    seen: set[str] = set()
    for root, is_user in _roots():
        for sk in _discover_root(root, is_user):
            d = sk.path.parent                      # atomic/<task>/<name>/
            if d.name in ("zeroshot", task) or d.parent.name != task:
                continue
            if d.parent.parent.name != "atomic":
                continue
            if not (d / "policy.py").exists() or sk.name in seen:
                continue
            seen.add(sk.name)
            out.append(sk)
    return out


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke the optional ``policy.py:run(**kwargs)`` next to a skill's SKILL.md.

    Returns a dict from the policy. If the skill has no ``policy.py``, raises.
    Skills without a policy are still useful: the agent reads SKILL.md and
    composes other tools on its own.
    """
    sk = get(name)
    if sk is None:
        raise ValueError(f"unknown skill '{name}'")
    policy_py = sk.path.parent / "policy.py"
    if not policy_py.exists():
        raise RuntimeError(
            f"skill '{name}' has no policy.py — read SKILL.md and compose tools instead"
        )
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_skill_{name}_policy", policy_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {policy_py}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    entry = getattr(mod, "run", None)
    if entry is None:
        raise RuntimeError(f"{policy_py} has no ``run()`` entry point")
    return entry(**kwargs) or {}
