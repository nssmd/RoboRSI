"""Command-line interface for the public LIBERO release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from roborsi_libero.catalog import SHORT_TASK_CATALOG
from roborsi_libero.config import (
    ReleaseConfig,
    detect_gpu_devices,
    load_config,
    write_config,
)
from roborsi_libero.evidence import default_manifest_path, replay_bundle

app = typer.Typer(
    name="roborsi",
    help="Configure, evaluate, and inspect roborsi on LIBERO short.",
    no_args_is_help=True,
)
results_app = typer.Typer(help="Replay and summarize retained evidence.")
eval_app = typer.Typer(help="Run fixed or adaptive LIBERO short evaluation.")
services_app = typer.Typer(help="Manage local motion-planning services.")
app.add_typer(results_app, name="results")
app.add_typer(eval_app, name="eval")
app.add_typer(services_app, name="services")
console = Console()


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
    console.print("Set OPENAI_API_KEY in your environment; no secret was written to disk.")


@eval_app.command("libero-short")
def eval_libero_short(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("roborsi.yaml"),
    mode: Annotated[str | None, typer.Option("--mode", help="adaptive or fixed")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Evaluate the complete 120-task LIBERO short catalog."""
    release = load_config(config)
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
    from roborsi_libero.launcher import launch_evaluation

    output = launch_evaluation(release, mode=resolved_mode)
    console.print(f"[green]Evaluation started[/green] {output}")


@results_app.command("replay")
def replay_results(
    manifest: Annotated[Path | None, typer.Option("--manifest", "-m")] = None,
    json_output: Annotated[Path | None, typer.Option("--json", help="Write result JSON.")] = None,
) -> None:
    """Recompute task-level metrics from retained simulator verdicts."""
    result = replay_bundle(manifest or default_manifest_path())
    payload = result.to_dict()
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

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
    table.add_row("Total", f"{result.solved_tasks}/{result.total_tasks}", f"{100*result.rate:.1f}%")
    console.print(table)


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
    from roborsi_libero.doctor import run_doctor

    report = run_doctor(
        load_config(config),
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


@app.command()
def dashboard(
    result: Annotated[
        Path | None, typer.Option("--result", help="Replay/result JSON path.")
    ] = None,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Open the local desktop results dashboard."""
    from roborsi_libero.dashboard import serve_dashboard

    serve_dashboard(result_path=result, host=host, port=port, open_browser=not no_browser)


@services_app.command("start")
def services_start(
    port: Annotated[int, typer.Option("--port")] = 5559,
) -> None:
    """Start the isolated PyRoKi solver and wait for readiness."""
    from roborsi_libero.services import start_service

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
    from roborsi_libero.services import service_status

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
    from roborsi_libero.services import stop_service

    status = stop_service(Path.cwd())
    console.print(f"PyRoKi {status.detail}; retained .runtime service records")


if __name__ == "__main__":
    app()
