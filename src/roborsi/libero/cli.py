"""Command-line interface for the public LIBERO release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from roborsi import __version__
from roborsi.libero.catalog import SHORT_TASK_CATALOG
from roborsi.libero.config import (
    ReleaseConfig,
    detect_gpu_devices,
    load_config,
    write_config,
)
from roborsi.libero.evidence import default_manifest_path, replay_bundle
from roborsi.libero.runs import (
    discover_campaigns,
    load_campaign_payload,
    resolve_campaign,
)

app = typer.Typer(
    name="roborsi",
    help="Configure, evaluate, and inspect RoboRSI on LIBERO short.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
results_app = typer.Typer(help="Replay and summarize retained evidence.")
eval_app = typer.Typer(help="Run fixed or adaptive LIBERO short evaluation.")
runs_app = typer.Typer(help="List and inspect local evaluation campaigns.")
services_app = typer.Typer(help="Manage local motion-planning services.")
visualize_app = typer.Typer(help="Render standalone evidence visualizations.")
app.add_typer(results_app, name="results")
app.add_typer(eval_app, name="eval")
app.add_typer(runs_app, name="runs")
app.add_typer(services_app, name="services")
app.add_typer(visualize_app, name="visualize")
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"RoboRSI {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed RoboRSI version.",
        ),
    ] = False,
) -> None:
    """Operate the RoboRSI LIBERO short reference runtime."""


def _load_config_or_exit(path: Path) -> ReleaseConfig:
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Configuration error[/red] {exc}")
        console.print("Run [bold]./roborsi configure --yes[/bold] to create roborsi.yaml.")
        raise typer.Exit(2) from exc


def _write_json(path: Path, payload: dict) -> Path:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


@app.command()
def configure(
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("roborsi.yaml"),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Overwrite without prompting.")] = False,
    base_url: Annotated[str | None, typer.Option("--base-url")] = None,
    api_key_env: Annotated[str, typer.Option("--api-key-env")] = "OPENAI_API_KEY",
    workers: Annotated[int | None, typer.Option("--workers", min=0)] = None,
    gpus: Annotated[
        str | None,
        typer.Option("--gpus", help="Comma-separated GPU indices or 'auto'."),
    ] = None,
    libero_root: Annotated[Path | None, typer.Option("--libero-root")] = None,
    results_root: Annotated[Path | None, typer.Option("--results-root")] = None,
) -> None:
    """Create the one canonical YAML configuration."""
    target = output.expanduser()
    if target.exists() and not yes:
        overwrite = typer.confirm(f"Overwrite {target}?")
        if not overwrite:
            raise typer.Abort()
    config = ReleaseConfig.default(repo_root=Path.cwd())
    if base_url is not None or api_key_env != config.provider.api_key_env:
        config = config.model_copy(
            update={
                "provider": config.provider.model_copy(
                    update={
                        "base_url": base_url or config.provider.base_url,
                        "api_key_env": api_key_env,
                    }
                )
            }
        )
    gpu_devices = config.runtime.gpu_devices
    if gpus is not None:
        if gpus.strip().lower() == "auto":
            gpu_devices = detect_gpu_devices()
        else:
            try:
                gpu_devices = [int(value.strip()) for value in gpus.split(",") if value.strip()]
            except ValueError as exc:
                raise typer.BadParameter("gpus must be comma-separated integers or 'auto'") from exc
    config = config.model_copy(
        update={
            "simulator": config.simulator.model_copy(
                update={"root": (libero_root or config.simulator.root).resolve()}
            ),
            "runtime": config.runtime.model_copy(
                update={
                    "results_root": (results_root or config.runtime.results_root).resolve(),
                    "workers": config.runtime.workers if workers is None else workers,
                    "gpu_devices": gpu_devices,
                }
            ),
        }
    )
    path = write_config(config, target)
    console.print(f"[green]Configuration written[/green] {path}")
    console.print(
        f"Set {config.provider.api_key_env} in your environment; no secret was written to disk."
    )


@eval_app.command("libero-short")
def eval_libero_short(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
    mode: Annotated[str | None, typer.Option("--mode", help="adaptive or fixed")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Evaluate the complete 120-task LIBERO short catalog."""
    release = _load_config_or_exit(config)
    resolved_mode = mode or release.evaluation.mode
    if resolved_mode not in {"adaptive", "fixed"}:
        raise typer.BadParameter("mode must be adaptive or fixed", param_hint="--mode")
    if dry_run:
        console.print(
            f"Dry run: {len(SHORT_TASK_CATALOG)} tasks, "
            f"{len(release.evaluation.seeds)} ordered seeds, mode={resolved_mode}."
        )
        console.print(f"Results root: {release.runtime.results_root}")
        return
    from roborsi.libero.launcher import launch_evaluation

    output = launch_evaluation(release, mode=resolved_mode)
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"[bold green]Evaluation started[/bold green]  {output.name}",
                    f"Run directory  {output}",
                    f"Status         ./roborsi status {output.name}",
                    f"Web console    ./roborsi web --run {output.name}",
                    f"Supervisor     tail -f {output / 'supervisor.log'}",
                ]
            ),
            title="RoboRSI",
            border_style="green",
        )
    )


@results_app.command("replay")
def replay_results(
    manifest: Annotated[Path | None, typer.Option("--manifest", "-m")] = None,
    json_output: Annotated[Path | None, typer.Option("--json", help="Write result JSON.")] = None,
) -> None:
    """Recompute task-level metrics from retained simulator verdicts."""
    result = replay_bundle(manifest or default_manifest_path())
    payload = result.to_dict()
    if json_output is not None:
        _write_json(json_output, payload)

    table = Table(title=f"{result.metric} ({result.claim_scope})")
    table.add_column("Suite")
    table.add_column("Solved", justify="right")
    table.add_column("Rate", justify="right")
    for suite, row in result.by_suite.items():
        table.add_row(
            suite,
            f"{row['solved_tasks']}/{row['total_tasks']}",
            f"{100 * float(row['rate']):.1f}%",
        )
    table.add_section()
    table.add_row(
        "Total", f"{result.solved_tasks}/{result.total_tasks}", f"{100 * result.rate:.1f}%"
    )
    console.print(table)


@runs_app.command("list")
def runs_list(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
) -> None:
    """List local campaigns, newest first."""
    release = _load_config_or_exit(config)
    campaigns = discover_campaigns(release.runtime.results_root)
    if not campaigns:
        console.print(f"No campaigns found in {release.runtime.results_root}")
        return
    table = Table(title="RoboRSI campaigns", header_style="bold")
    table.add_column("Run")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Passes", justify="right")
    table.add_column("Coverage", justify="right")
    for campaign in campaigns:
        table.add_row(
            campaign.run_id,
            campaign.mode,
            campaign.status.upper(),
            f"{campaign.completed_passes}/{campaign.protocol_passes}",
            f"{campaign.solved_tasks}/{campaign.total_tasks}",
        )
    console.print(table)


@app.command()
def status(
    run: Annotated[
        str | None,
        typer.Argument(help="Campaign id or path. Defaults to the latest run."),
    ] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
    json_output: Annotated[
        Path | None,
        typer.Option("--json", help="Write normalized status JSON."),
    ] = None,
) -> None:
    """Show one campaign's current status and resource totals."""
    release = _load_config_or_exit(config)
    try:
        campaign = resolve_campaign(release.runtime.results_root, run)
        payload = load_campaign_payload(campaign)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Campaign error[/red] {exc}")
        raise typer.Exit(2) from exc
    if json_output is not None:
        destination = _write_json(json_output, payload)
        console.print(f"[green]Status JSON written[/green] {destination}")

    verdicts = dict(payload.get("verdicts") or {})
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Run", str(payload["run_id"]))
    table.add_row("Status", str(payload["status"]).upper())
    table.add_row("Mode", str(payload["mode"]))
    table.add_row(
        "Passes",
        f"{payload['completed_passes']}/{payload['k']}",
    )
    table.add_row(
        "Coverage",
        f"{payload['solved_tasks']}/{payload['total_tasks']} ({100 * float(payload['rate']):.1f}%)",
    )
    table.add_row(
        "Verdicts",
        (
            f"{int(verdicts.get('task_success', 0))} success · "
            f"{int(verdicts.get('task_failure', 0))} failure · "
            f"{int(verdicts.get('implementation_failure', 0))} implementation · "
            f"{int(verdicts.get('infrastructure_excluded', 0))} infrastructure"
        ),
    )
    table.add_row(
        "Resources",
        (
            f"{int(payload.get('total_tokens', 0)):,} tokens · "
            f"{int(payload.get('total_vlm_calls', 0)):,} VLM calls · "
            f"{float(payload.get('total_elapsed_s', 0.0)) / 3600:.2f} episode hours"
        ),
    )
    table.add_row("Directory", str(campaign))
    console.print(
        Panel(
            table,
            title=f"RoboRSI · {payload['run_id']}",
            border_style=(
                "green"
                if payload["status"] == "complete"
                else "yellow"
                if payload["status"] == "running"
                else "red"
            ),
        )
    )


@visualize_app.command("skill-tree")
def visualize_skill_tree(
    storyboard: Annotated[
        Path | None,
        typer.Option("--storyboard", help="Optional RoboRSI storyboard JSON."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Standalone HTML output."),
    ] = Path("roborsi-skill-tree.html"),
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Render the interactive RoboRSI skill-evolution tree."""
    import webbrowser

    from roborsi.libero.skill_tree import write_skill_tree_html

    destination = write_skill_tree_html(output, storyboard_path=storyboard)
    console.print(f"[green]Skill tree written[/green] {destination}")
    if not no_browser:
        webbrowser.open(destination.as_uri())


@app.command()
def doctor(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
    offline: Annotated[
        bool, typer.Option("--offline", help="Skip provider network probe.")
    ] = False,
    no_services: Annotated[
        bool, typer.Option("--no-services", help="Skip local service probes.")
    ] = False,
    replay_only: Annotated[
        bool, typer.Option("--replay-only", help="Check only replay dependencies.")
    ] = False,
) -> None:
    """Check configuration, simulator paths, services, and provider access."""
    from roborsi.libero.doctor import run_doctor

    report = run_doctor(
        _load_config_or_exit(config),
        offline=offline,
        check_services=not no_services and not replay_only,
        check_simulator=not replay_only,
    )
    for check in report.checks:
        color = "green" if check.ok else "red"
        verdict = "PASS" if check.ok else "FAIL"
        console.print(f"[{color}]{verdict}[/{color}] {check.name}: {check.detail}")
    if not report.ok:
        raise typer.Exit(1)


def _open_web(
    *,
    config: Path,
    run: str | None,
    result: Path | None,
    public: bool,
    host: str,
    port: int,
    output: Path | None,
    no_browser: bool,
) -> None:
    selected = sum((run is not None, result is not None, public))
    if selected > 1:
        raise typer.BadParameter("choose only one of --run, --result, or --public")

    campaign_root: Path | None = None
    result_path: Path | None = result
    if run is not None:
        release = _load_config_or_exit(config)
        try:
            campaign_root = resolve_campaign(release.runtime.results_root, run)
        except (OSError, ValueError) as exc:
            console.print(f"[red]Campaign error[/red] {exc}")
            raise typer.Exit(2) from exc
    elif not public and result is None:
        try:
            release = load_config(config)
            campaigns = discover_campaigns(release.runtime.results_root)
        except (OSError, ValueError):
            campaigns = []
        if campaigns:
            campaign_root = campaigns[0].path

    import webbrowser

    from roborsi.libero.dashboard import serve_dashboard, write_dashboard_html

    if output is not None:
        destination = write_dashboard_html(
            output,
            result_path=result_path,
            campaign_root=campaign_root,
        )
        console.print(f"[green]Web console written[/green] {destination}")
        if campaign_root is not None:
            console.print(f"Source campaign: {campaign_root.name}")
        if not no_browser:
            webbrowser.open(destination.as_uri())
        return
    try:
        serve_dashboard(
            result_path=result_path,
            campaign_root=campaign_root,
            host=host,
            port=port,
            open_browser=not no_browser,
        )
    except RuntimeError as exc:
        console.print(f"[red]Web console error[/red] {exc}")
        raise typer.Exit(2) from exc


@app.command()
def web(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
    run: Annotated[
        str | None,
        typer.Option("--run", help="Campaign id or directory."),
    ] = None,
    result: Annotated[
        Path | None, typer.Option("--result", help="Replay/result JSON path.")
    ] = None,
    public: Annotated[
        bool,
        typer.Option("--public", help="Force the packaged public evidence."),
    ] = False,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8765,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write standalone HTML and exit."),
    ] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Open the Web console for the latest run or packaged evidence."""
    _open_web(
        config=config,
        run=run,
        result=result,
        public=public,
        host=host,
        port=port,
        output=output,
        no_browser=no_browser,
    )


@services_app.command("start")
def services_start(
    port: Annotated[int, typer.Option("--port")] = 5559,
) -> None:
    """Start the isolated PyRoKi solver and wait for readiness."""
    from roborsi.libero.services import start_service

    status = start_service(Path.cwd(), port=port)
    console.print(
        f"[{'green' if status.running else 'yellow'}]"
        f"PyRoKi {status.detail}[/] pid={status.pid} port={status.port}"
    )
    if not status.running:
        raise typer.Exit(1)


@services_app.command("status")
def services_status() -> None:
    """Show local solver readiness."""
    from roborsi.libero.services import service_status

    status = service_status(Path.cwd())
    console.print(
        f"[{'green' if status.running else 'red'}]"
        f"PyRoKi {status.detail}[/] pid={status.pid or '-'} port={status.port}"
    )
    if not status.running:
        raise typer.Exit(1)


@services_app.command("stop")
def services_stop() -> None:
    """Stop the managed solver while preserving its log and state record."""
    from roborsi.libero.services import stop_service

    status = stop_service(Path.cwd())
    console.print(f"PyRoKi {status.detail}; retained .runtime service records")


if __name__ == "__main__":
    app(prog_name="roborsi")
