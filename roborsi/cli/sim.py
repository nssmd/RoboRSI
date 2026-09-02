"""``roborsi sim`` — direct simulator access (no skill layer).

Use for debugging: list backends / tasks, spin up an env and play the
expert without going through the DataStore. Skill-level runs go through
``roborsi skill run`` with ``--params '{"mode":"collect",...}'``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.embodied.agent_loop import (
    BackendUnavailable,
    get_backend,
    list_backends,
)


sim_app = typer.Typer(name="sim", help="Simulator backends (RoboTwin, ...).", no_args_is_help=True)
console = Console()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    else:
        console.print_json(data=data, default=str)


@sim_app.command("list")
def list_cmd(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List registered sim backends and their availability."""
    rows: list[dict[str, Any]] = []
    for name in list_backends():
        try:
            be = get_backend(name)
            ok, reason = be.available()
        except BackendUnavailable as exc:
            ok, reason = False, str(exc)
        rows.append({"backend": name, "available": ok, "reason": reason})
    if as_json:
        _emit({"backends": rows}, as_json=True)
        return
    table = Table(title="Sim backends")
    table.add_column("Backend", style="cyan")
    table.add_column("Available", style="green")
    table.add_column("Reason / hint", style="dim")
    for r in rows:
        table.add_row(
            r["backend"],
            "[green]yes[/green]" if r["available"] else "[red]no[/red]",
            r["reason"] or "-",
        )
    console.print(table)


@sim_app.command("tasks")
def tasks(
    backend: str = typer.Argument("robotwin"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List tasks exposed by <backend>."""
    be = get_backend(backend)
    names = be.list_tasks()
    if as_json:
        _emit({"backend": backend, "tasks": names}, as_json=True)
        return
    console.print(f"[bold]{backend}[/] exposes {len(names)} tasks:")
    for n in names:
        console.print(f"  {n}")


@sim_app.command("run")
def run_cmd(
    task: str = typer.Argument(..., help="Task name, e.g. beat_block_hammer."),
    backend: str = typer.Option("robotwin", "--backend"),
    seed: int = typer.Option(0, "--seed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run the scripted expert once; print success + obs summary."""
    be = get_backend(backend)
    with be.make_env(task) as env:
        rollout = env.run_expert(seed=seed)
    summary = {
        "task": task,
        "backend": backend,
        "seed": seed,
        "success": rollout.success,
        "outcome": rollout.outcome,
        "steps": rollout.length,
        "meta": rollout.meta,
        "cameras": sorted({c for s in rollout.steps for c in s.obs.images}) if rollout.steps else [],
    }
    _emit(summary, as_json)
