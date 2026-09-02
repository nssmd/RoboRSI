"""``roborsi skill`` — Typer subapp for skills."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from roborsi.embodied.skills import Skill, discover, get, run


skill_app = typer.Typer(name="skill", help="Skill catalog (docs + policies).", no_args_is_help=True)
console = Console()


def _emit(data: Any, as_json: bool, human_renderer=None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return
    if human_renderer:
        human_renderer()
    else:
        console.print_json(data=data)


@skill_app.command("list")
def list_(
    category: str = typer.Option("", "--category", "-c", help="Filter by category."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List all known skills."""
    skills = discover()
    if category:
        skills = [s for s in skills if s.category == category]
    if as_json:
        _emit({"skills": [s.to_dict() for s in skills]}, as_json=True)
        return
    if not skills:
        console.print("[dim]No skills found.[/]")
        return
    table = Table(title="Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Source", style="magenta")
    table.add_column("Description", style="white")
    for s in skills:
        table.add_row(s.name, s.category or "-", "user" if s.is_user else "shipped", s.description)
    console.print(table)


@skill_app.command("show")
def show(
    name: str = typer.Argument(..., help="Skill name."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print SKILL.md for <name>."""
    sk = get(name)
    if sk is None:
        console.print(f"[red]✗[/] Unknown skill '{name}'.")
        raise typer.Exit(1)
    if as_json:
        _emit({**sk.to_dict(), "frontmatter": sk.frontmatter, "body": sk.body}, as_json=True)
        return
    console.print(f"[bold]{sk.name}[/] [dim]({sk.category or 'uncategorised'}, {'user' if sk.is_user else 'shipped'})[/]")
    console.print()
    console.print(Markdown(sk.body))


@skill_app.command("run")
def run_cmd(
    name: str = typer.Argument(..., help="Skill name."),
    params: str = typer.Option("{}", "--params", help="JSON kwargs for the skill's policy.run()."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Execute <name>'s ``policy.py:run(**kwargs)`` if present."""
    try:
        kwargs = json.loads(params)
        if not isinstance(kwargs, dict):
            raise ValueError("--params must be a JSON object")
    except ValueError as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1)
    try:
        result = run(name, **kwargs)
    except (ValueError, RuntimeError) as exc:
        _emit({"ok": False, "error": str(exc)}, as_json, human_renderer=lambda: console.print(f"[red]✗[/] {exc}"))
        raise typer.Exit(1)
    _emit({"ok": True, "result": result}, as_json, human_renderer=lambda: console.print_json(data=result))


# ────────────────────────────────────────────────────────────────────────
# VLM-authored skill review (human-in-the-loop)
# ────────────────────────────────────────────────────────────────────────


@skill_app.command("review")
def review_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """List pending VLM-authored skill proposals awaiting human review."""
    from roborsi.embodied.skills._lib.human_review.skill_review import list_pending
    pend = list_pending()
    if as_json:
        import json as _j
        console.print(_j.dumps(pend, indent=2))
        return
    if not pend:
        console.print("[green]No pending skill proposals.[/]")
        return
    for d in pend:
        console.print(f"\n[bold]{d['id']}[/]  task={d.get('task_name','?')}")
        console.print(f"  name: [cyan]{d['name']}[/]")
        console.print(f"  doc:  {d.get('docstring','')}")
        console.print(f"  submitted: {d.get('submitted_at')}")
        console.print(f"  --- code ---")
        for line in (d.get('code') or '').splitlines():
            console.print(f"    {line}")
        console.print(f"  approve: roborsi-sim skill approve {d['id']}")
        console.print(f"  reject:  roborsi-sim skill reject {d['id']} 'reason'")


@skill_app.command("approve")
def approve_cmd(proposal_id: str = typer.Argument(...),
                note: str = typer.Argument("")) -> None:
    """Approve a pending VLM-authored skill proposal."""
    from roborsi.embodied.skills._lib.human_review.skill_review import approve
    if approve(proposal_id, note):
        console.print(f"[green]✓ approved {proposal_id}[/]")
    else:
        console.print(f"[red]✗ proposal {proposal_id} not found[/]")
        raise typer.Exit(1)


@skill_app.command("reject")
def reject_cmd(proposal_id: str = typer.Argument(...),
               note: str = typer.Argument(...,
                   help="reason (will be surfaced to VLM)")) -> None:
    """Reject a pending VLM-authored skill proposal with a reason."""
    from roborsi.embodied.skills._lib.human_review.skill_review import reject
    if reject(proposal_id, note):
        console.print(f"[red]✗ rejected {proposal_id}[/]: {note}")
    else:
        console.print(f"[red]✗ proposal {proposal_id} not found[/]")
        raise typer.Exit(1)


@skill_app.command("review-server")
def review_server_cmd(
    port: int = typer.Option(8765, "--port", help="HTTP port"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Start a local HTML review UI for VLM-authored skill proposals.
    
    Open http://localhost:8765 in a browser. Each pending proposal renders
    with code highlighting + Approve/Reject buttons that flip the queue
    file inline. Run alongside your sim trial.
    """
    from roborsi.embodied.skills._lib.human_review.review_server import serve
    serve(port=port, host=host)


@skill_app.command("feishu-bot")
def feishu_bot_cmd(
    port: int = typer.Option(9876, "--port"),
    host: str = typer.Option("0.0.0.0", "--host"),
) -> None:
    """[Legacy webhook] Start Feishu bot HTTP event server. Requires public URL (port-forward / ngrok). Prefer `feishu-bot-ws` for zero-port setup."""
    from roborsi.channels.agent.feishu.bot_server import serve
    serve(port=port, host=host)


@skill_app.command("feishu-bot-ws")
def feishu_bot_ws_cmd() -> None:
    """Start Feishu bot via WebSocket long-connection (NO port, NO tunnel). Dials out to Feishu — works from any laptop/internal server."""
    from roborsi.channels.agent.feishu.bot_ws import serve
    serve()


@skill_app.command("monitor")
def monitor_cmd(
    port: int = typer.Option(8770, "--port"),
    host: str = typer.Option("0.0.0.0", "--host"),
) -> None:
    """Start HTML dashboard at :8770 showing all RoboRSI task runs (live status, frames, demo video)."""
    from roborsi.channels.agent.feishu.status_server import serve
    serve(port=port, host=host)
