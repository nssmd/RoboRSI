"""Skill library — CaP-X-inspired auto extraction of proven tool-call recipes.

After each successful episode, the trace writer (LHExecutor / rollout
runtime) persists a trace.json. This module scans those traces per atomic
skill, builds
canonical tool-call sequence templates, counts occurrences, and promotes
those that appear ≥min_occurrences times as "proven recipes" injected
into future system prompts.

Output format (single recipe):
  {tool: 'localize_object_top_center', args_keys: ['object', 'grid_n']}
  → {tool: 'is_reachable',              args_keys: ['arm', 'x', 'y', 'z']}
  → ...
  → {tool: 'done',                       args_keys: ['success']}

CODE-AS-POLICY ADDITIONS (CaP-X-style):
- promote_functions_from_code(task, code): AST-parses Python source from
  successful code-as-policy runs, extracts function defs, persists them
  with occurrence count.
- load_function_library(task): returns promoted function source ready to
  inject into next system prompt.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import data_root


@dataclass
class ProvenRecipe:
    tool_sequence: list[str]              # ordered tool names
    occurrences: int
    source_run_ids: list[str]


def extract_tool_sequence(trace_path: Path) -> list[str]:
    """Extract the ordered tool_call sequence from a single trace.json.

    Filters out repeated identical adjacent calls (e.g. retry loops) and
    pure-observation calls (look/find_pixel) — keeps action+structural
    calls only, which form the recipe skeleton."""
    if not trace_path.exists():
        return []
    OBSERVE_ONLY = {"look", "scan_wrist", "capture_image"}
    seq: list[str] = []
    try:
        trace = json.loads(trace_path.read_text())
    except Exception:
        return []
    if not isinstance(trace, list):
        return []
    for step in trace:
        if not isinstance(step, dict):
            continue
        tc = step.get("tool_call")
        if not isinstance(tc, dict):
            continue
        name = tc.get("tool")
        if not name or name in OBSERVE_ONLY:
            continue
        if seq and seq[-1] == name:
            continue   # de-dup repeated identical calls
        seq.append(name)
    return seq


def get_proven_recipes(atomic_skill: str, *, min_occurrences: int = 2,
                       max_recipes: int = 5) -> list[ProvenRecipe]:
    """Scan DataStore for successful episodes of `atomic_skill`,
    extract tool sequences, return promoted recipes (≥min_occurrences)."""
    released_root = data_root()
    if not released_root.exists():
        return []
    # Episode dirs: <root>/<skill>/<run_id>/{trace.json, meta.json, ...}
    skill_dir = released_root / atomic_skill
    if not skill_dir.exists():
        return []
    # tuple(sequence) → list[run_id]
    by_seq: dict[tuple[str, ...], list[str]] = {}
    for run_dir in sorted(skill_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        if not bool(meta.get("success", False)):
            continue
        seq = extract_tool_sequence(run_dir / "trace.json")
        if not seq:
            continue
        key = tuple(seq)
        by_seq.setdefault(key, []).append(run_dir.name)
    promoted = []
    for seq_tuple, run_ids in sorted(by_seq.items(),
                                      key=lambda kv: -len(kv[1])):
        if len(run_ids) >= min_occurrences:
            promoted.append(ProvenRecipe(
                tool_sequence=list(seq_tuple),
                occurrences=len(run_ids),
                source_run_ids=run_ids,
            ))
        if len(promoted) >= max_recipes:
            break
    return promoted


def format_recipes_for_prompt(recipes: list[ProvenRecipe],
                                atomic_skill: str) -> str:
    """Render proven recipes as a system-prompt section."""
    if not recipes:
        return ""
    lines = [
        f"PROVEN RECIPES (auto-extracted from past successful runs of "
        f"'{atomic_skill}', appearing in ≥2 successful episodes):"
    ]
    for i, r in enumerate(recipes, 1):
        chain = " → ".join(r.tool_sequence)
        lines.append(f"  [{i}] (used in {r.occurrences} successful runs): {chain}")
    lines.append(
        "These are PROVEN tool-call orderings. Prefer them over freelancing "
        "unless the scene clearly differs. Adapt the args, not the structure."
    )
    return "\n".join(lines) + "\n\n"


# ────────────────────────────────────────────────────────────────────────
# Code-as-Policy function library (CaP-X style)
# ────────────────────────────────────────────────────────────────────────


def _function_library_path(task_name: str) -> Path:
    return data_root() / task_name / "_function_library.json"


def load_function_library(task_name: str) -> list[dict[str, Any]]:
    """Load promoted function source code for this task (CaP-X style).
    Returns list of {name, code, docstring, occurrences} for promoted
    functions (occurrences >= 2)."""
    p = _function_library_path(task_name)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    return [f for f in data.get("functions", []) if f.get("occurrences", 0) >= 2]


def promote_functions_from_code(task_name: str, code: str,
                                  *, min_occurrences: int = 2,
                                  ) -> list[str]:
    """AST-parse the VLM-emitted Python program from a successful trial,
    extract top-level def statements, merge into the persistent library
    with occurrence counting. Returns list of newly-promoted function
    names (those whose count crossed the min_occurrences threshold)."""
    from roborsi.runtime_mode import require_evolution
    require_evolution("promoting code into the released function library")
    p = _function_library_path(task_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            existing = {}
    by_name: dict[str, dict[str, Any]] = {
        f["name"]: f for f in existing.get("functions", [])
    }

    newly_promoted: list[str] = []
    for name, src, doc in _extract_top_level_functions(code):
        if name in by_name:
            entry = by_name[name]
            entry["occurrences"] = int(entry.get("occurrences", 1)) + 1
            entry["code"] = src    # always store latest version
            if doc:
                entry["docstring"] = doc
            crossed = (entry["occurrences"] == min_occurrences)
            if crossed:
                newly_promoted.append(name)
        else:
            by_name[name] = {"name": name, "code": src, "docstring": doc,
                              "occurrences": 1}
    out = {"functions": list(by_name.values())}
    p.write_text(json.dumps(out, indent=2))
    return newly_promoted


def _extract_top_level_functions(code: str
                                  ) -> list[tuple[str, str, str]]:
    """Return [(name, full_source, docstring)] for every TOP-LEVEL `def`
    in the given Python source. Nested defs / class methods ignored."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    lines = code.splitlines()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        start = node.lineno - 1
        end = node.end_lineno if getattr(node, "end_lineno", None) else None
        if end is None:
            # Conservative fallback: find next dedented line.
            end = start + 1
            while end < len(lines) and (not lines[end] or
                                          lines[end].startswith((" ", "\t"))):
                end += 1
        src = "\n".join(lines[start:end])
        doc = ast.get_docstring(node) or ""
        out.append((node.name, src, doc))
    return out
