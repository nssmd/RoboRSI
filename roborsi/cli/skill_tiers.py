"""``roborsi base / atomic / long-horizon`` — three-tier skill CLI.

Every skill has the same execution surface (``policy.run(**params)``); these
subcommands are convenience wrappers that target the right namespace and
expose the right defaults / inspection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from roborsi.embodied.skills import Skill, discover, get
from roborsi.embodied.skills import run as run_skill


console = Console()


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    else:
        console.print_json(data=data, default=str)


def _filter(predicate) -> list[Skill]:
    return [s for s in discover() if predicate(s)]


def _is_in_category(skill: Skill, cat: str) -> bool:
    return skill.category == cat or str(skill.path).find(f"/skills/{cat}/") >= 0


def _by_parent(skill: Skill, parent: str) -> bool:
    fm = skill.frontmatter or {}
    return str(fm.get("parent", "")) == parent


def _show(name: str) -> None:
    sk = get(name)
    if sk is None:
        console.print(f"[red]✗[/] skill '{name}' not found")
        raise typer.Exit(1)
    console.print(f"[bold]{sk.name}[/] [dim]{sk.frontmatter.get('kind', '-')}[/]")
    console.print(Markdown(sk.body))


def _run(name: str, params: str, as_json: bool) -> None:
    try:
        kwargs = json.loads(params)
        if not isinstance(kwargs, dict):
            raise ValueError("--params must be a JSON object")
    except ValueError as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc
    try:
        result = run_skill(name, **kwargs)
    except (ValueError, RuntimeError, NotImplementedError) as exc:
        _emit({"ok": False, "error": str(exc)}, as_json)
        raise typer.Exit(1) from exc
    _emit({"ok": True, "result": result}, as_json)


# ────────────────────────────────────────────────────────────────────────
# `roborsi base`
# ────────────────────────────────────────────────────────────────────────


base_app = typer.Typer(name="base", help="Base skills — robot primitives (capture, move, gripper, ...).", no_args_is_help=True)


@base_app.command("list")
def base_list(
    robot: str = typer.Option("", "--robot", "-r", help="Filter to a specific robot backend."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List all base skills, optionally filtered by robot backend."""
    rows = []
    for sk in _filter(lambda s: _is_in_category(s, "base")):
        fm_robot = (sk.frontmatter or {}).get("robot", "?")
        if robot and fm_robot != robot:
            continue
        rows.append({"robot": fm_robot, "name": sk.name, "description": sk.description, "path": str(sk.path)})
    if as_json:
        _emit({"base_skills": rows}, as_json)
        return
    table = Table(title="Base skills")
    table.add_column("Robot", style="cyan")
    table.add_column("Name", style="magenta")
    table.add_column("Description", style="white")
    for r in rows:
        table.add_row(r["robot"], r["name"], r["description"])
    console.print(table)


@base_app.command("show")
def base_show(name: str) -> None:
    """Print SKILL.md of a base skill."""
    _show(name)


@base_app.command("run")
def base_run(
    name: str = typer.Argument(...),
    params: str = typer.Option("{}", "--params"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Invoke a base skill's policy.run(**params). Note: most base skills need an active env (passed via Python, not CLI)."""
    _run(name, params, as_json)


@base_app.command("new")
def base_new(
    name: str = typer.Argument(..., help="New base skill name (snake_case)."),
    robot: str = typer.Option("robotwin", "--robot"),
    category: str = typer.Option("control", "--category", help="perception | geometry | control | policy | active_perception"),
    description: str = typer.Option("", "--description", help="One-line description for the auto-prompt."),
    overwrite: bool = typer.Option(False, "--overwrite"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Scaffold a new base skill — SKILL.md + policy.py stub. Tier 2 uses this when the existing toolset can't solve a phase."""
    from roborsi.agent.lifecycle.scaffold import SKILLS_ROOT
    base_dir = SKILLS_ROOT / "base" / robot / name
    if base_dir.exists() and not overwrite:
        _emit({"ok": False, "reason": f"{base_dir} exists; pass --overwrite"}, as_json)
        raise typer.Exit(1)
    base_dir.mkdir(parents=True, exist_ok=True)
    desc = description or f"{name.replace('_', ' ')} (TODO: describe)"
    skill_md = f"""---
name: {name}
kind: base
robot: {robot}
category: {category}
version: 0.1.0
description: {desc}
args:
  # TODO: declare args. Example:
  # arm: {{ type: string, required: true, enum: [left, right] }}
returns:
  ok: bool
when_to_use: |
  TODO: describe when the VLM should call this tool. The auto-prompt picks
  this up and shows it to the VLM as `When to use: ...`.
---

# {name} · {robot}

TODO: describe the implementation. After editing this SKILL.md, also wire
the dispatch in `roborsi/embodied/sim/robotwin/robotwin_agent.py`:

  1. Add a branch to `_dispatch()`:
       if name == "{name}": return _do_{name}(state, args)
  2. Implement `_do_{name}(state, args) -> tuple[dict, Observation]` next to other `_do_*`.
  3. Restart any in-flight runs so the new tool is picked up by `_build_tool_specs()`.
"""
    policy_py = f'''"""base.{robot}.{name} — policy.py (Tier 2 scaffold).

Most base skills register as VLM tools via `_dispatch` in robotwin_agent.py.
The `run()` here is for Python callers (debug / direct invocation). The actual
tool execution path is `_do_{name}(state, args)` in robotwin_tools.py.
"""

from __future__ import annotations

from typing import Any


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError(
        "TODO: implement {name}. Edit this file AND add `_do_{name}` to "
        "robotwin_tools.py + dispatch wiring."
    )
'''
    (base_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (base_dir / "policy.py").write_text(policy_py, encoding="utf-8")
    _emit({
        "ok": True, "name": name, "robot": robot, "category": category,
        "dir": str(base_dir),
        "files": [str(base_dir / "SKILL.md"), str(base_dir / "policy.py")],
        "next_steps": [
            f"1. Edit {base_dir}/SKILL.md — fill in args schema + when_to_use.",
            f"2. Implement `_do_{name}(state, args)` in robotwin_tools.py.",
            f"3. Add dispatch branch: if name == '{name}': return _do_{name}(state, args).",
            "4. Re-run any task — the auto-prompt picks up the new tool.",
        ],
    }, as_json)


# ────────────────────────────────────────────────────────────────────────
# `roborsi atomic`
# ────────────────────────────────────────────────────────────────────────


atomic_app = typer.Typer(name="atomic", help="Atomic tasks (zeroshot / train / eval / reset_success / reset_failure).", no_args_is_help=True)


_ATOMIC_PHASES = ("zeroshot", "train", "eval", "reset_success", "reset_failure")


@atomic_app.command("list")
def atomic_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List atomic tasks (top-level SKILL.md, not the sub-skills)."""
    rows = []
    for sk in _filter(lambda s: (s.frontmatter or {}).get("kind") == "atomic"):
        rows.append({
            "name": sk.name,
            "domain": (sk.frontmatter or {}).get("domain", "-"),
            "description": sk.description,
            "active_executor_default": ((sk.frontmatter or {}).get("metadata") or {}).get("active_executor", {}).get("default"),
        })
    if as_json:
        _emit({"atomics": rows}, as_json)
        return
    table = Table(title="Atomic tasks")
    table.add_column("Name", style="cyan")
    table.add_column("Domain", style="magenta")
    table.add_column("Default executor", style="green")
    table.add_column("Description", style="white")
    for r in rows:
        table.add_row(r["name"], r["domain"], r["active_executor_default"] or "-", r["description"])
    console.print(table)


@atomic_app.command("inspect")
def atomic_inspect(
    name: str = typer.Argument(..., help="Atomic task name."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the atomic + which sub-skills are present."""
    sk = get(name)
    if sk is None or (sk.frontmatter or {}).get("kind") != "atomic":
        console.print(f"[red]✗[/] '{name}' not an atomic task")
        raise typer.Exit(1)
    sub_skills = list(_filter(lambda x: _by_parent(x, name)))
    rows = []
    for phase in _ATOMIC_PHASES:
        match = next((s for s in sub_skills if (s.frontmatter or {}).get("phase") == phase), None)
        rows.append({"phase": phase, "present": bool(match), "name": match.name if match else None})

    if as_json:
        _emit({"task": name, "phases": rows}, as_json)
        return
    table = Table(title=f"atomic · {name}")
    table.add_column("Phase", style="cyan")
    table.add_column("Present", style="green")
    table.add_column("Name", style="magenta")
    for r in rows:
        table.add_row(r["phase"], "✓" if r["present"] else "[dim]—[/dim]", r["name"] or "-")
    console.print(table)


@atomic_app.command("run")
def atomic_run(
    task: str = typer.Argument(..., help="Atomic task name."),
    phase: str = typer.Argument(..., help="One of: zeroshot, train, eval, reset_success, reset_failure."),
    params: str = typer.Option("{}", "--params"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Invoke a sub-skill of an atomic task: e.g. ``atomic run beat_block_hammer zeroshot --params '{...}'``."""
    if phase not in _ATOMIC_PHASES:
        console.print(f"[red]✗[/] phase must be one of {_ATOMIC_PHASES}, got {phase!r}")
        raise typer.Exit(1)
    qualified = f"{task}.{phase}"
    _run(qualified, params, as_json)


@atomic_app.command("new")
def atomic_new(
    task: str = typer.Argument(..., help="New atomic task name (snake_case)."),
    sim_task: str = typer.Option(..., "--sim-task", help="Underlying BiCoord/RoboTwin task name."),
    backend: str = typer.Option("bicoord", "--backend"),
    spec: str = typer.Option("", "--spec", help="Natural-language description of the task."),
    judge_criterion: str = typer.Option("", "--judge", help="What counts as success (the judge prompt)."),
    overwrite: bool = typer.Option(False, "--overwrite"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Scaffold a new atomic task — full 4-piece set + judge populated from a brief spec."""
    from roborsi.agent.lifecycle.scaffold import scaffold_atomic
    res = scaffold_atomic(task_name=task, sim_task=sim_task, backend=backend,
                          spec=spec, judge_criterion=judge_criterion, overwrite=overwrite)
    _emit(res, as_json)


@atomic_app.command("status")
def atomic_status(
    task: str = typer.Argument(...),
    episode_target: int = typer.Option(15, "--episode-target"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the lifecycle state of an atomic task."""
    from roborsi.agent.lifecycle.atomic import status
    _emit(status(task, episode_target=episode_target), as_json)


@atomic_app.command("spin")
def atomic_spin(
    task: str = typer.Argument(...),
    sim_task: str = typer.Option(..., "--sim-task"),
    backend: str = typer.Option("bicoord", "--backend"),
    seeds: str = typer.Option("1-25", "--seeds", help="Range like '1-25' or comma-list '1,3,5'."),
    success_target: float = typer.Option(0.40, "--success-target"),
    episode_target: int = typer.Option(15, "--episode-target"),
    train_steps: int = typer.Option(2000, "--train-steps"),
    long_horizon: str = typer.Option("", "--long-horizon", help="If set, drive collection via this LH task's execute."),
    max_iterations: int = typer.Option(50, "--max-iter"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Drive an atomic task to ACTIVE state — the data flywheel."""
    from roborsi.agent.lifecycle.driver import drive_atomic
    seed_list = _parse_seeds(seeds)
    res = drive_atomic(
        task_name=task, sim_task=sim_task, backend=backend,
        seeds=seed_list, success_target=success_target,
        episode_target=episode_target, train_steps=train_steps,
        long_horizon_wrapper=long_horizon or None,
        max_iterations=max_iterations, dry_run=dry_run,
    )
    _emit(res, as_json)


def _parse_seeds(s: str) -> list[int]:
    s = s.strip()
    if "-" in s and "," not in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",") if x.strip()]


# ────────────────────────────────────────────────────────────────────────
# `roborsi long-horizon`
# ────────────────────────────────────────────────────────────────────────


long_horizon_app = typer.Typer(name="long-horizon", help="Long-horizon tasks (plan / progress_judge / posttrain).", no_args_is_help=True)


_LH_PHASES = ("plan", "progress_judge", "posttrain", "execute")


@long_horizon_app.command("list")
def lh_list(as_json: bool = typer.Option(False, "--json")) -> None:
    rows = []
    for sk in _filter(lambda s: (s.frontmatter or {}).get("kind") == "long_horizon"):
        rows.append({
            "name": sk.name,
            "domain": (sk.frontmatter or {}).get("domain", "-"),
            "description": sk.description,
        })
    if as_json:
        _emit({"long_horizon": rows}, as_json)
        return
    table = Table(title="Long-horizon tasks")
    table.add_column("Name", style="cyan")
    table.add_column("Domain", style="magenta")
    table.add_column("Description", style="white")
    for r in rows:
        table.add_row(r["name"], r["domain"], r["description"])
    console.print(table)


@long_horizon_app.command("inspect")
def lh_inspect(
    name: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    sk = get(name)
    if sk is None or (sk.frontmatter or {}).get("kind") != "long_horizon":
        console.print(f"[red]✗[/] '{name}' not a long-horizon task")
        raise typer.Exit(1)
    subs = [s for s in _filter(lambda x: _by_parent(x, name))]
    rows = []
    for phase in _LH_PHASES:
        match = next((s for s in subs if (s.frontmatter or {}).get("phase") == phase), None)
        rows.append({"phase": phase, "present": bool(match), "name": match.name if match else None})
    if as_json:
        _emit({"task": name, "phases": rows}, as_json)
        return
    table = Table(title=f"long-horizon · {name}")
    table.add_column("Phase", style="cyan")
    table.add_column("Present", style="green")
    table.add_column("Name", style="magenta")
    for r in rows:
        table.add_row(r["phase"], "✓" if r["present"] else "[dim]—[/dim]", r["name"] or "-")
    console.print(table)


@long_horizon_app.command("run")
def lh_run(
    task: str = typer.Argument(...),
    phase: str = typer.Argument(...),
    params: str = typer.Option("{}", "--params"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    if phase not in _LH_PHASES:
        console.print(f"[red]✗[/] phase must be one of {_LH_PHASES}, got {phase!r}")
        raise typer.Exit(1)
    qualified = f"{task}.{phase}"
    _run(qualified, params, as_json)
