"""``roborsi farm`` — parallel collection across workers."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.embodied.farm import run as farm_run


farm_app = typer.Typer(name="farm", help="Parallel Farm: multi-worker data collection.", no_args_is_help=True)
console = Console()


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    else:
        console.print_json(data=data, default=str)


@farm_app.command("run")
def run_cmd(
    skill: str = typer.Argument(..., help="Lifecycle skill to farm (usually 'expert_replay')."),
    workers: int = typer.Option(4, "--workers", "-w"),
    episodes_per_worker: int = typer.Option(25, "--episodes"),
    seed_start: int = typer.Option(0, "--seed-start"),
    params: str = typer.Option("{}", "--params", help='JSON kwargs forwarded to the skill, e.g. \'{"task":"beat_block_hammer"}\'.'),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run <skill> on N workers in parallel with disjoint seed shards."""
    try:
        extra = json.loads(params)
        if not isinstance(extra, dict):
            raise ValueError("--params must be a JSON object")
    except ValueError as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc

    report = farm_run(
        skill,
        workers=workers,
        episodes_per_worker=episodes_per_worker,
        seed_start=seed_start,
        params=extra,
    )
    if as_json:
        _emit(report, as_json=True)
        return

    console.print(
        f"[bold]{skill}[/] × [cyan]{workers}[/] workers × "
        f"[cyan]{episodes_per_worker}[/] ep = "
        f"[green]{report['total_successes']}[/]/{report['total_episodes']} "
        f"success ({report['success_rate']:.0%})"
    )
    table = Table(title="Per-worker")
    table.add_column("Worker", style="cyan")
    table.add_column("Seed range", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Note", style="dim")
    for r in report["reports"]:
        rng = f"{r['seed_range'][0]}..{r['seed_range'][1] - 1}"
        note = r.get("error", "") or _note_from_ok(r)
        table.add_row(str(r["worker_id"]), rng, r["status"], note)
    console.print(table)


def _note_from_ok(report: dict[str, Any]) -> str:
    if report.get("status") != "ok":
        return ""
    result = report.get("result") or {}
    eps = result.get("episodes") or []
    succ = sum(1 for e in eps if e.get("success"))
    return f"{succ}/{len(eps)} success"
