#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for import_root in (ROOT, SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs  # noqa: E402
from roborsi.embodied.skills import discover_compounds, get_ns  # noqa: E402

FORBIDDEN = {
    "check_success",
    "region_box",
    "parsed_problem",
    "_to_robot0_eef_pos",
    "goal_state",
}
ALLOWED_RAW_OBS_POS_KEYS = {
    "robot0_eef_pos",
    "robot0_joint_pos",
}
META_TOOLS = {
    "done",
    "read_skill_code",
    "list_base_skills",
    "propose_new_skill",
    "propose_skill_update",
}
EXPECTED_VISIBLE_TOOL_NAMES = frozenset(
    {
        "descend_tcp_to_z",
        "close_drawer",
        "done",
        "execute_previewed_move",
        "find_by_detector",
        "find_by_pointing",
        "find_pixel",
        "get_arm_pose",
        "get_grasp_pose",
        "grasp_object",
        "gripper",
        "home",
        "is_holding",
        "is_reachable",
        "list_base_skills",
        "look",
        "measure_distance",
        "measure_relative_rotation",
        "measure_vector",
        "mark_orbit_point",
        "move_ee_delta",
        "move_to_pixel",
        "move_to_pose",
        "observe_orbit",
        "open_hinged_door",
        "place_beside",
        "place_held_at_target_servo",
        "place_object_in",
        "place_on_surface",
        "propose_new_skill",
        "propose_skill_update",
        "preview_move_to_pose",
        "pull_drawer",
        "push_object",
        "recover_joint_posture",
        "read_joint_state",
        "read_skill_code",
        "rotate_vector",
        "unproject_pixel",
        "verify_pick_complete",
    }
)
FROZEN_DISABLED_META_TOOLS = frozenset(
    {
        "read_skill_code",
        "list_base_skills",
        "propose_new_skill",
        "propose_skill_update",
    }
)


def _visible_compound_skills(*, frozen: bool | None = None):
    import os

    is_frozen = (
        os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") != "0"
        if frozen is None
        else bool(frozen)
    )
    if (
        os.environ.get("ROBORSI_ATOMIC_COMPOUND", "1") != "1"
        or is_frozen
    ):
        return []
    return discover_compounds("libero_pick_place")


def expected_visible_tool_names(*, frozen: bool | None = None) -> frozenset[str]:
    import os

    is_frozen = (
        os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") != "0"
        if frozen is None
        else bool(frozen)
    )
    expected = EXPECTED_VISIBLE_TOOL_NAMES
    if is_frozen:
        return expected - FROZEN_DISABLED_META_TOOLS
    return expected | frozenset(
        skill.name for skill in _visible_compound_skills(frozen=False)
    )


def _resolve_visible_skill(name: str):
    base = get_ns(name, "libero")
    if base is not None:
        return base
    return next(
        (skill for skill in _visible_compound_skills() if skill.name == name),
        None,
    )


FORBIDDEN_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ground truth",
        re.compile(r"\bground[-\u2011\u2013\u2014 ]?truth\b", re.IGNORECASE),
    ),
    ("check_success", re.compile(r"\bcheck_success\b", re.IGNORECASE)),
    (
        "simulator object positions",
        re.compile(
            r"\bsimulator\s+object\s+positions?\b",
            re.IGNORECASE,
        ),
    ),
    ("_to_robot0_eef_pos", re.compile(r"\b[a-z0-9_]*_to_robot0_eef_pos\b", re.IGNORECASE)),
    ("object_z", re.compile(r"\bobject_z\b", re.IGNORECASE)),
    ("to_eef", re.compile(r"\bto[-_ ]?eef\b", re.IGNORECASE)),
    ("object-to-eef", re.compile(r"object[-_ ]to[-_ ]eef", re.IGNORECASE)),
    ("world z", re.compile(r"\bworld\s+z\b", re.IGNORECASE)),
    ("object pose", re.compile(r"\bobject\s+pose\b", re.IGNORECASE)),
    ("describe_scene", re.compile(r"\bdescribe_scene\b", re.IGNORECASE)),
    ("get_object_pose", re.compile(r"\bget_object_pose\b", re.IGNORECASE)),
    (
        "ground-truth poses",
        re.compile(r"\bground[-\u2011\u2013\u2014 ]?truth\s+poses\b", re.IGNORECASE),
    ),
    (
        "ground-truth points",
        re.compile(r"\bground[-\u2011\u2013\u2014 ]?truth\s+points\b", re.IGNORECASE),
    ),
    (
        "ground-truth read",
        re.compile(r"\bground[-\u2011\u2013\u2014 ]?truth\s+read\b", re.IGNORECASE),
    ),
    ("OSC", re.compile(r"\bOSC\b", re.IGNORECASE)),
    ("仿真判定", re.compile(r"仿真判定")),
)


@dataclass(frozen=True)
class Finding:
    tool: str
    path: Path
    line: int
    symbol: str

    def fmt(self) -> str:
        rel = self.path
        return f"{self.tool}: {rel}:{self.line} -> {self.symbol}"


def _visible_libero_policy_files() -> list[tuple[str, Path]]:
    specs = _build_tool_specs(ns="libero", task="libero_pick_place")
    out: list[tuple[str, Path]] = []
    for spec in specs:
        name = spec["function"]["name"]
        if name in META_TOOLS:
            continue
        sk = _resolve_visible_skill(name)
        if sk is None:
            continue
        policy = sk.path.parent / "policy.py"
        if policy.exists():
            out.append((name, policy))
    return out


def libero_helper_policy_files() -> list[Path]:
    root = ROOT / "src/roborsi/embodied/skills/base/_lib/libero"
    return sorted(root.glob("*.py"))


def scan_libero_helper_policies() -> list[str]:
    findings: list[str] = []
    for path in libero_helper_policy_files():
        findings.extend(scan_policy_path(f"_lib/{path.stem}", path))
    return sorted(set(findings))


def libero_atomic_skill_docs() -> list[Path]:
    root = ROOT / "src/roborsi/embodied/skills/atomic"
    return sorted(
        path
        for parent in root.glob("libero*")
        if parent.is_dir()
        for path in parent.rglob("SKILL.md")
        if path.is_file()
    )


def scan_libero_atomic_skill_docs() -> list[str]:
    findings: list[str] = []
    for path in libero_atomic_skill_docs():
        findings.extend(scan_skill_doc_path(path.parent.name, path))
    return sorted(set(findings))


def _visible_libero_skill_docs() -> list[tuple[str, Path]]:
    specs = _build_tool_specs(ns="libero", task="libero_pick_place")
    out: list[tuple[str, Path]] = []
    for spec in specs:
        name = spec["function"]["name"]
        if name in META_TOOLS:
            continue
        sk = _resolve_visible_skill(name)
        if sk is None:
            continue
        doc = sk.path
        if doc.exists():
            out.append((name, doc))
    return out


def _scan_ast(path: Path) -> Iterable[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def has_subscript_ancestor(node: ast.AST) -> bool:
        cur = node
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, ast.Subscript):
                return True
        return False

    raw_obs_readers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Attribute) and node.value.attr == "raw_obs":
            raw_obs_readers.add(node.targets[0].id)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in raw_obs_readers
                and node.targets[0].id not in raw_obs_readers
            ):
                raw_obs_readers.add(node.targets[0].id)
                changed = True

    def is_raw_source_call(value: ast.AST | None) -> bool:
        return bool(
            _is_raw_obs_call(value)
            or (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in raw_obs_readers
            )
        )

    raw_obs_names: set[str] = set()
    raw_container_names: set[str] = set()

    def is_env_raw_reference(value: ast.AST | None) -> bool:
        if isinstance(value, ast.Attribute) and value.attr == "_raw":
            return True
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
            and len(value.args) >= 2
            and _extract_lookup_key(value.args[1]) == "_raw"
        ):
            return True
        if (
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Attribute)
            and value.value.attr == "__dict__"
            and _extract_lookup_key(value.slice) == "_raw"
        ):
            return True
        return False

    def is_raw_reference(value: ast.AST | None) -> bool:
        if is_env_raw_reference(value):
            return True
        if isinstance(value, ast.Name):
            return value.id in raw_obs_names
        if isinstance(value, ast.Subscript):
            base = value.value
            if isinstance(base, ast.Name):
                return base.id in raw_container_names
            return is_raw_reference(base)
        return False

    def carries_raw_obs(value: ast.AST | None) -> bool:
        if value is None:
            return False
        if is_raw_source_call(value) or is_env_raw_reference(value):
            return True
        if is_raw_reference(value):
            return True
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "copy"
            and is_raw_reference(value.func.value)
        ):
            return True
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return any(carries_raw_obs(item) for item in value.elts)
        if isinstance(value, ast.Dict):
            return any(
                carries_raw_obs(item)
                for item in [*value.keys, *value.values]
                if item is not None
            )
        return False

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name in raw_obs_names:
                continue
            if carries_raw_obs(node.value):
                raw_obs_names.add(name)
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                    raw_container_names.add(name)
                elif (
                    isinstance(node.value, ast.Subscript)
                    and is_raw_reference(node.value)
                ):
                    raw_container_names.add(name)
                elif (
                    isinstance(node.value, ast.Name)
                    and node.value.id in raw_container_names
                ):
                    raw_container_names.add(name)
                changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            raw_args = [
                arg
                for arg in (
                    list(node.args)
                    + [kw.value for kw in node.keywords]
                )
                if carries_raw_obs(arg)
            ]
            if raw_args:
                yield node.lineno, "raw_obs alias passthrough"
            if is_raw_source_call(node):
                raw_parent = parent.get(node)
                allowed_raw_context = (
                    (
                        isinstance(raw_parent, ast.Assign)
                        and len(raw_parent.targets) == 1
                        and isinstance(raw_parent.targets[0], ast.Name)
                    )
                    or isinstance(raw_parent, ast.Subscript)
                    or (
                        isinstance(raw_parent, ast.Attribute)
                        and raw_parent.attr == "get"
                    )
                )
                if not allowed_raw_context:
                    yield node.lineno, "raw_obs passthrough"
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN:
                yield node.lineno, func.attr
            if isinstance(func, ast.Name) and func.id in FORBIDDEN:
                yield node.lineno, func.id
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and (
                    is_raw_reference(func.value)
                    or is_env_raw_reference(func.value)
                )
                and func.attr not in {"get", "copy"}
            ):
                yield node.lineno, f"raw_obs method: {func.attr}"
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and (
                    is_raw_reference(func.value)
                    or is_raw_source_call(func.value)
                    or is_env_raw_reference(func.value)
                )
            ):
                key = _extract_lookup_key(node.args[0] if node.args else None)
                if _is_forbidden_object_pos_key(key):
                    yield node.lineno, "_pos"
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and is_env_raw_reference(func.value)
            ):
                key = _extract_lookup_key(node.args[0] if node.args else None)
                if _is_forbidden_object_pos_key(key):
                    yield node.lineno, "_raw object position"
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            yield node.lineno, node.attr
        if isinstance(node, ast.Assign) and carries_raw_obs(node.value):
            if any(not isinstance(target, ast.Name) for target in node.targets):
                yield node.lineno, "raw_obs alias assignment"
        if (
            isinstance(node, ast.Return)
            and carries_raw_obs(node.value)
        ):
            yield node.lineno, "raw_obs alias return"
        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in raw_obs_names
            ):
                key = _extract_lookup_key(node.slice)
                if _is_forbidden_object_pos_key(key):
                    yield node.lineno, "_pos"
            if (
                is_env_raw_reference(node.value)
            ):
                key = _extract_lookup_key(node.slice)
                if _is_forbidden_object_pos_key(key):
                    yield node.lineno, "_raw object position"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if "_to_robot0_eef_pos" in text and has_subscript_ancestor(node):
                yield node.lineno, "_to_robot0_eef_pos"
            if "goal_state" in text and has_subscript_ancestor(node):
                yield node.lineno, "goal_state"


def _is_raw_obs_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "raw_obs"


def _is_raw_obs_get(node: ast.Call, raw_obs_names: set[str]) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id in raw_obs_names
    return _is_raw_obs_call(value)


def _is_raw_obs_subscript(node: ast.Subscript, raw_obs_names: set[str]) -> bool:
    value = node.value
    if isinstance(value, ast.Name):
        return value.id in raw_obs_names
    return _is_raw_obs_call(value)


def _extract_lookup_key(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _extract_lookup_key(node.left) or ""
        right = _extract_lookup_key(node.right) or ""
        return left + right if left or right else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
        return "".join(parts) if parts else None
    return None


def _is_forbidden_object_pos_key(key: str | None) -> bool:
    if not key:
        return False
    if key in ALLOWED_RAW_OBS_POS_KEYS:
        return False
    return key.endswith("_pos")


def scan_policy_path(tool: str, path: Path) -> list[str]:
    findings: list[str] = []
    for line, symbol in _scan_ast(Path(path)):
        findings.append(Finding(tool, Path(path), line, symbol).fmt())
    return sorted(set(findings))


def scan_visible_libero_policies() -> list[str]:
    findings: list[str] = []
    for tool, path in _visible_libero_policy_files():
        findings.extend(scan_policy_path(tool, path))
    findings.extend(scan_libero_helper_policies())
    return sorted(set(findings))


def scan_visible_tool_manifest() -> list[str]:
    specs = _build_tool_specs(ns="libero", task="libero_pick_place")
    names = [str(row["function"]["name"]) for row in specs]
    seen: set[str] = set()
    findings: list[str] = []
    for name in names:
        if name in seen:
            findings.append(f"duplicate tool: {name}")
        seen.add(name)

    expected = expected_visible_tool_names()

    for name in sorted(seen - expected):
        findings.append(f"unexpected tool: {name}")
    for name in sorted(expected - seen):
        findings.append(f"missing tool: {name}")

    shipped_base = (
        Path(__file__).resolve().parents[1]
        / "src/roborsi/embodied/skills/base"
    ).resolve()
    shipped_atomic = (
        Path(__file__).resolve().parents[1]
        / "src/roborsi/embodied/skills/atomic"
    ).resolve()
    for row in specs:
        fn = row["function"]
        name = str(fn["name"])
        if not str(fn.get("description") or "").strip():
            findings.append(f"empty description: {name}")
        if name in META_TOOLS:
            continue
        sk = _resolve_visible_skill(name)
        if sk is None:
            findings.append(f"unresolved shipped tool: {name}")
            continue
        source = sk.path.resolve()
        shipped = False
        for root in (shipped_base, shipped_atomic):
            try:
                source.relative_to(root)
                shipped = True
                break
            except ValueError:
                continue
        if not shipped:
            findings.append(f"non-shipped tool source: {name}: {sk.path}")
    return sorted(set(findings))


def scan_skill_doc_path(tool: str, path: Path) -> list[str]:
    text = Path(path).read_text()
    findings: list[str] = []
    lines = text.splitlines()
    for symbol, pattern in FORBIDDEN_SKILL_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            line_text = lines[line - 1].lower() if 0 < line <= len(lines) else ""
            if symbol == "world z" and ("object" not in line_text and "pose" not in line_text):
                continue
            findings.append(Finding(tool, Path(path), line, symbol).fmt())
    return sorted(set(findings))


def scan_visible_libero_skill_docs() -> list[str]:
    findings: list[str] = []
    for tool, path in _visible_libero_skill_docs():
        findings.extend(scan_skill_doc_path(tool, path))
    return sorted(set(findings))


def collect_findings() -> list[str]:
    return sorted(
        set(
            scan_visible_tool_manifest()
            + scan_visible_libero_policies()
            + scan_visible_libero_skill_docs()
            + scan_libero_atomic_skill_docs()
        )
    )


def main() -> int:
    findings = collect_findings()
    if not findings:
        print("libero gt leak audit: clean")
        return 0
    print("libero gt leak audit: findings")
    for row in findings:
        print(row)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
