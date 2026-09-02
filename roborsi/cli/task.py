"""``roborsi task`` — task bundle runner (list/inspect/run)."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.embodied.bundle import BundleError, execute, load
from roborsi.embodied.skills import discover


task_app = typer.Typer(name="task", help="Task bundle runner (lifecycle skills).", no_args_is_help=True)
console = Console()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    else:
        console.print_json(data=data, default=str)


@task_app.command("list")
def list_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """List all skills with kind=task."""
    tasks = [s for s in discover() if s.frontmatter.get("kind") == "task"]
    if as_json:
        _emit(
            {"tasks": [{
                "name": s.name,
                "description": s.description,
                "domain": s.frontmatter.get("domain", s.category),
                "path": str(s.path),
            } for s in tasks]},
            as_json=True,
        )
        return
    table = Table(title="Tasks")
    table.add_column("Name", style="cyan")
    table.add_column("Domain", style="green")
    table.add_column("Description", style="white")
    for s in tasks:
        table.add_row(s.name, str(s.frontmatter.get("domain", s.category)), s.description)
    console.print(table)


@task_app.command("inspect")
def inspect(
    name: str = typer.Argument(..., help="Task name."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the bundle pipeline for a task."""
    try:
        bundle = load(name)
    except BundleError as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc
    if as_json:
        _emit({
            "task": bundle.task,
            "source": str(bundle.source),
            "stages": [s.__dict__ for s in bundle.stages],
        }, as_json=True)
        return
    console.print(f"[bold]{bundle.task}[/] [dim]({bundle.source})[/]")
    table = Table(title="Pipeline")
    table.add_column("#", style="dim")
    table.add_column("Stage", style="cyan")
    table.add_column("Skill", style="magenta")
    table.add_column("Enabled", style="green")
    table.add_column("DependsOn", style="yellow")
    table.add_column("Params", style="white")
    for i, s in enumerate(bundle.stages):
        table.add_row(
            str(i),
            s.stage,
            s.skill,
            "yes" if s.enabled else "[dim]no[/dim]",
            ",".join(s.depends_on) or "-",
            json.dumps(s.params, ensure_ascii=False),
        )
    console.print(table)


@task_app.command("run")
def run_cmd(
    name: str = typer.Argument(..., help="Task name."),
    only: str = typer.Option("", "--only", help="Run only this stage."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print resolved plan, don't execute."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Execute a task's bundle, sequentially."""
    try:
        bundle = load(name)
    except BundleError as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc

    def _on_start(stage, sk):
        console.print(f"[cyan]▶[/] {stage.stage} → {sk.name}")

    def _on_end(stage, res):
        status = res.get("status", "?")
        colour = {"ok": "green", "skeleton": "yellow", "skipped": "dim",
                  "blocked": "red", "failed": "red", "dry-run": "cyan"}.get(status, "white")
        console.print(f"  [{colour}]{status}[/{colour}]  {stage.stage}")

    summary = execute(
        bundle,
        only=only or None,
        dry_run=dry_run,
        on_stage_start=None if as_json else _on_start,
        on_stage_end=None if as_json else _on_end,
    )
    _emit(summary, as_json)
