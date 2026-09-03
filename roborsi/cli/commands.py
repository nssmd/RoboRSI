"""CLI commands for roborsi."""

import os
import sys
from pathlib import Path
from typing import Any

# Force UTF-8 encoding for CLI stdio
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import typer
from rich.console import Console
from rich.table import Table

from roborsi import __logo__, __version__
from roborsi.config.paths import get_workspace_path
from roborsi.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="roborsi",
    help=f"{__logo__} RoboRSI - Robot Self-Evolution Harness",
    no_args_is_help=False,
    invoke_without_command=True,
)

console = Console()

# Register sub-apps
from roborsi.cli.dev import dev_app
from roborsi.cli.camera import camera_app
from roborsi.cli.skill import skill_app
from roborsi.cli.sim import sim_app
from roborsi.cli.task import task_app
from roborsi.cli.farm import farm_app
from roborsi.cli.libero import libero_app
from roborsi.cli.skill_tiers import base_app, atomic_app, long_horizon_app
from roborsi.cli.bench import bench_app
from roborsi.cli.bench_lh import bench_lh_app
from roborsi.cli.selfevo import selfevo_app

app.add_typer(dev_app, name="dev", help="Developer utilities (reset workspace, etc.).")
app.add_typer(camera_app, name="camera", help="Camera capture (iPhone via Record3D).")
app.add_typer(skill_app, name="skill", help="Skill catalog (docs + policies).")
app.add_typer(sim_app, name="sim", help="Simulator backends (RoboTwin, ...).")
app.add_typer(task_app, name="task", help="Task bundle runner (collection→dataset→train→eval→rl).")
app.add_typer(farm_app, name="farm", help="Parallel Farm: multi-worker data collection.")
app.add_typer(libero_app, name="libero", help="Configure and verify LIBERO.")
app.add_typer(base_app, name="base", help="Base skills — robot primitives.")
app.add_typer(atomic_app, name="atomic", help="Atomic tasks (zeroshot/train/eval/reset_*).")
app.add_typer(long_horizon_app, name="long-horizon", help="Long-horizon tasks (plan/judge/posttrain).")
app.add_typer(bench_app, name="bench", help="Benchmark a skill: success rate + tool calls.")
app.add_typer(bench_lh_app, name="bench-lh", help="Long-horizon benchmark (claim 3).")
app.add_typer(selfevo_app, name="selfevo", help="Self-evolution loop (extract base skills from successful traces).")


@app.command()
def manager(
    model: str = typer.Option("claude-opus-4-8", help="Model for the Manager session."),
    backend: str = typer.Option(None, "--backend",
                                 help="Manager backend: claude|codex|copilot "
                                      "(default: $ROBORSI_ROLE_BACKEND or claude)."),
    resume: bool = typer.Option(False, "--resume", "-r",
                                 help="Pick a previous manager session to resume."),
    cont: bool = typer.Option(False, "--continue", "-c",
                               help="Resume the most-recently-active manager session."),
) -> None:
    """Launch a Manager — a top-level agent session that oversees the
    self-evolution flywheel, reviews proposals, and proposes improvements.

    Default starts a NEW manager; ``--resume`` picks a previous one from a list;
    ``--continue`` resumes the most recent. Managers are backend-agnostic
    (claude/codex/copilot). Every launch is registered so the CLI picker and the
    web cockpit show the same manager list. It recommends; you approve."""
    from roborsi.agents.manager import sessions as msess
    repo = Path(__file__).resolve().parents[2]
    manager_md = (repo / "roborsi" / "agents" / "manager" / "MANAGER.md").read_text(encoding="utf-8")
    backend = backend or os.environ.get("ROBORSI_ROLE_BACKEND", "claude")
    # The Manager IS the operator's own session — strip any inherited sim-agent
    # Keep provider credentials in the operator's environment.
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(key, None)

    if resume or cont:
        sess = _pick_manager(msess.list_manager_sessions(), auto=cont)
        if sess is None:
            typer.echo("No previous manager sessions — starting a new one.")
        else:
            _launch_manager(sess.backend, model, manager_md, sess.cwd, resume_id=sess.id)
            return
    # New: pre-assign an id, register it, launch fresh.
    import uuid
    sid = str(uuid.uuid4())
    msess.register_manager(sid, backend=backend, cwd=str(repo))
    _launch_manager(backend, model, manager_md, str(repo), new_id=sid)


def _pick_manager(sessions: list, *, auto: bool):
    """Interactive picker over manager sessions (or the most-recent when auto)."""
    if not sessions:
        return None
    if auto:
        return sessions[0]
    import time
    typer.echo("Previous manager sessions:")
    for i, s in enumerate(sessions, 1):
        age = (time.time() - s.last_active) / 60
        typer.echo(f"  {i}. [{s.backend}·{s.topic}] {s.id[:8]} · {age:.0f}min ago · {s.label}")
    raw = typer.prompt("Resume which? (number, or blank for new)", default="").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(sessions)):
        return None
    return sessions[int(raw) - 1]


def _launch_manager(backend: str, model: str, manager_md: str, cwd: str,
                    *, new_id: str | None = None, resume_id: str | None = None) -> None:
    """Exec the manager's backend CLI (replaces this process; hands over the TTY).
    claude is fully wired; codex/copilot are backend-aware launch points."""
    os.chdir(cwd)   # session is keyed to this project dir
    if backend == "claude":
        argv = ["claude", "--name", "RoboRSI-Manager",
                "--append-system-prompt", manager_md,
                "--add-dir", str(Path.home() / ".roborsi"),
                "--model", model, "--permission-mode", "acceptEdits"]
        if new_id:
            argv += ["--session-id", new_id]
        elif resume_id:
            argv += ["--resume", resume_id]
        os.execvp("claude", argv)
    if backend in ("codex", "copilot"):
        # Backend-aware launch point — the interactive CLI for these backends.
        # Resume-by-id semantics differ per tool; wire precisely when a manager
        # actually runs on one (both live managers are currently claude).
        argv = [backend] + (["resume", resume_id] if resume_id else [])
        os.execvp(backend, argv)
    raise typer.BadParameter(f"unknown manager backend {backend!r}")


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} RoboRSI v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """RoboRSI robot self-evolution harness. Bare `roborsi` opens the Manager cockpit."""
    if ctx.invoked_subcommand is None:
        _launch_tui()


def _launch_tui(host: str = "127.0.0.1", port: int = 8791,
                no_bridge: bool = False) -> None:
    """Open the Ink Manager cockpit (delegates to the stdlib-only launcher)."""
    from roborsi.cli.tui_launch import launch
    launch(host=host, port=port, no_bridge=no_bridge)


@app.command()
def tui(
    host: str = typer.Option("127.0.0.1", help="Dashboard bridge host."),
    port: int = typer.Option(8791, help="Dashboard bridge port."),
    no_bridge: bool = typer.Option(False, help="Don't auto-start the bridge (connect to an existing one)."),
) -> None:
    """Open the RoboRSI Manager cockpit (Ink terminal UI)."""
    _launch_tui(host=host, port=port, no_bridge=no_bridge)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    evo_port: int = typer.Option(8787, "--evo-port", min=1, max=65535),
    cockpit_port: int = typer.Option(8795, "--cockpit-port", min=1, max=65535),
    token: str | None = typer.Option(None, "--token", envvar="ROBORSI_WEB_TOKEN"),
    evo_only: bool = typer.Option(False, "--evo-only"),
    cockpit_only: bool = typer.Option(False, "--cockpit-only"),
) -> None:
    """Serve the evolution dashboard and Manager session cockpit."""
    if evo_only and cockpit_only:
        raise typer.BadParameter("--evo-only and --cockpit-only are exclusive")
    from roborsi.embodied.board.web.server import serve

    serve(
        host=host,
        evo_port=None if cockpit_only else evo_port,
        cockpit_port=None if evo_only else cockpit_port,
        auth_token=token,
    )


@app.command("eval")
def eval_task(
    task: str = typer.Argument(..., help="Atomic task name."),
    seeds: int = typer.Option(1, "--seeds", "-n", min=1),
    seed_start: int = typer.Option(0, "--seed-start"),
    tool_budget: int = typer.Option(40, "--tool-budget", min=1),
    backend: str | None = typer.Option(
        None, "--backend", help="Override the task's declared backend."
    ),
    sim_task: str | None = typer.Option(
        None, "--sim-task", help="Override the simulator task identifier."
    ),
    planner_model: str | None = typer.Option(None, "--planner-model"),
    engineer_model: str | None = typer.Option(None, "--engineer-model"),
    reviewer_model: str | None = typer.Option(None, "--reviewer-model"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Evaluate a frozen RoboRSI release without self-evolution."""
    import json
    from roborsi.evaluation.atomic import campaign_exit_code, run_atomic_campaign

    def _progress(name: str, seed: int, index: int, total: int) -> None:
        if not as_json:
            console.print(
                f"[dim]eval[/dim] {name} seed={seed} ({index}/{total})"
            )

    summary = run_atomic_campaign(
        task=task,
        seeds=seeds,
        seed_start=seed_start,
        mode="eval",
        tool_budget=tool_budget,
        backend=backend,
        sim_task=sim_task,
        planner_model=planner_model,
        engineer_model=engineer_model,
        reviewer_model=reviewer_model,
        progress=_progress,
    )
    if as_json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
    else:
        table = Table(title=f"Frozen eval · {task}")
        table.add_column("seed", justify="right")
        table.add_column("result")
        table.add_column("outcome")
        table.add_column("calls", justify="right")
        table.add_column("run_id")
        for row in summary["runs"]:
            label = {
                "success": "[green]PASS[/green]",
                "failure": "[red]FAIL[/red]",
                "infra": "[yellow]INFRA[/yellow]",
                "implementation_error": "[magenta]BUG[/magenta]",
            }[row["verdict"]]
            table.add_row(
                str(row["seed"]),
                label,
                str(row["outcome"]),
                str(row["tool_calls"]),
                str(row["run_id"] or ""),
            )
        console.print(table)
        rate = (
            f"{summary['success_rate'] * 100:.1f}%"
            if summary["success_rate"] is not None
            else "n/a"
        )
        console.print(
            f"[bold]Frozen result:[/bold] {summary['seeds_passed']}/"
            f"{summary['verdict_count']} "
            f"({rate}) · failures={summary['seeds_failed']} · "
            f"infra={summary['infra_count']} · "
            f"bugs={summary['implementation_error_count']} · "
            "no capability write-back"
        )
        console.print(f"[dim]manifest[/dim] {summary['manifest_path']}")
    exit_code = campaign_exit_code(summary)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command("eval-suite")
def eval_suite(
    backend: str = typer.Option("libero-pro", "--backend"),
    atomic: str = typer.Option("libero_pick_place", "--atomic"),
    pass_at: int = typer.Option(5, "--pass-at", "--seeds", min=1),
    seed_start: int = typer.Option(0, "--seed-start"),
    workers: int = typer.Option(4, "--workers", "-w", min=1),
    tool_budget: int = typer.Option(40, "--tool-budget", min=1),
    tasks: list[str] | None = typer.Option(None, "--task"),
    out_dir: Path | None = typer.Option(None, "--out"),
    infra_retries: int = typer.Option(2, "--infra-retries", min=0),
    planner_model: str | None = typer.Option(None, "--planner-model"),
    engineer_model: str | None = typer.Option(None, "--engineer-model"),
    reviewer_model: str | None = typer.Option(None, "--reviewer-model"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run resumable task-level pass@K on LIBERO short tasks."""
    import json

    from roborsi.evaluation.suite import (
        run_libero_short_suite,
        suite_exit_code,
    )

    def _progress(
        task_key: str,
        seed: int,
        completed: int,
        pending: int,
        row: dict[str, Any],
        solved: int,
        total: int,
    ) -> None:
        if as_json:
            return
        console.print(
            f"[dim]suite[/dim] seed={seed} {completed}/{pending} "
            f"{task_key} → {row['verdict']} · solved={solved}/{total}"
        )

    try:
        summary = run_libero_short_suite(
            backend=backend,
            atomic=atomic,
            seeds=pass_at,
            seed_start=seed_start,
            workers=workers,
            tool_budget=tool_budget,
            tasks=tasks,
            out_dir=out_dir,
            infra_retries=infra_retries,
            planner_model=planner_model,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            progress=_progress,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if as_json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
    else:
        table = Table(title=f"LIBERO short · task pass@{pass_at}")
        table.add_column("group")
        table.add_column("solved", justify="right")
        table.add_column("total", justify="right")
        for label, row in summary["subset"].items():
            table.add_row(f"subset/{label}", str(row["solved"]), str(row["total"]))
        for label, row in summary["suite"].items():
            table.add_row(f"suite/{label}", str(row["solved"]), str(row["total"]))
        console.print(table)
        console.print(
            f"[bold]Task pass@{pass_at}:[/bold] "
            f"{summary['tasks_solved']}/{summary['tasks_total']} "
            f"({summary['task_success_rate'] * 100:.1f}%) · "
            f"infra={summary['infra_count']} · "
            f"bugs={summary['implementation_error_count']}"
        )
        console.print(f"[dim]journal[/dim] {summary['journal_path']}")
        console.print(f"[dim]summary[/dim] {summary['summary_path']}")
    exit_code = suite_exit_code(summary)
    if exit_code:
        raise typer.Exit(exit_code)


# ============================================================================
# Onboard / Setup
# ============================================================================


def run_onboard_core(*, interactive: bool = True, skip_config: bool = False) -> None:
    """Core onboard logic shared by ``roborsi onboard`` and ``roborsi dev reset``.

    When *interactive* is False the config is always created fresh (no
    overwrite prompt) and the "next steps" banner is suppressed.
    When *skip_config* is True the config file is left untouched.
    """
    from roborsi.config.loader import get_config_path, load_config, save_config
    from roborsi.config.schema import Config

    config_path = get_config_path()

    if skip_config:
        pass
    elif config_path.exists() and interactive:
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            save_config(Config())
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    console.print("[dim]Config template now uses `maxTokens` + `contextWindowTokens`; `memoryWindow` is no longer a runtime setting.[/dim]")

    _onboard_plugins(config_path)

    workspace = get_workspace_path()
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")
    sync_workspace_templates(workspace)

    from roborsi.embodied.embodiment.manifest.helpers import get_manifest_path
    if not get_manifest_path().exists():
        console.print("[dim]Scanning hardware...[/dim]")
        from roborsi.embodied.embodiment.manifest import Manifest
        Manifest().ensure()
        from roborsi.embodied.embodiment.hardware.scan import scan_cameras, scan_serial_ports
        n_ports = len(scan_serial_ports())
        n_cameras = len(scan_cameras())
        console.print(f"[green]✓[/green] Embodied setup created ({n_ports} serial port(s), {n_cameras} camera(s) detected)")

    if interactive:
        console.print(f"\n{__logo__} RoboRSI is ready!")
        console.print("\nNext steps:")
        console.print("  1. Add your API key to [cyan]~/.roborsi/config.json[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print("  2. Chat: [cyan]roborsi agent -m \"Hello!\"[/cyan]")
        console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/nssmd/robo-rsi#-chat-apps[/dim]")


@app.command()
def onboard():
    """Initialize roborsi configuration and workspace."""
    run_onboard_core(interactive=True)


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively fill in missing values from defaults without overwriting user config."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from roborsi.channels.registry import discover_all

    all_channels = discover_all()
    if not all_channels:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        if name not in channels:
            channels[name] = cls.default_config()
        else:
            channels[name] = _merge_missing_defaults(channels[name], cls.default_config())

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from roborsi.channels.registry import discover_all
    from roborsi.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")

    for name, cls in sorted(discover_all().items()):
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from roborsi.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # roborsi/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall roborsi")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import shutil
    import subprocess

    from roborsi.config.loader import load_config
    from roborsi.config.paths import get_runtime_subdir

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    wa_cfg = getattr(config.channels, "whatsapp", None) or {}
    bridge_token = wa_cfg.get("bridgeToken", "") if isinstance(wa_cfg, dict) else getattr(wa_cfg, "bridge_token", "")
    if bridge_token:
        env["BRIDGE_TOKEN"] = bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js.[/red]")
        raise typer.Exit(1)

    try:
        subprocess.run([npm_path, "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list():
    """List all discovered channels (built-in and plugins)."""
    from roborsi.channels.registry import discover_all, discover_channel_names
    from roborsi.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled", style="green")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show roborsi status."""
    from roborsi.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} RoboRSI Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from roborsi.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


def _oauth_print(s: str) -> None:
    """Print with Rich, but use plain print for URLs to avoid wrapping."""
    if s.startswith("http://") or s.startswith("https://"):
        print(s)
    else:
        console.print(s)


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
):
    """Authenticate with an OAuth provider."""
    from roborsi.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler(force=force)


@_register_login("openai_codex")
def _login_openai_codex(force: bool = False) -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)

    if not force:
        try:
            token = get_token()
        except RuntimeError:
            token = None
        if token and token.access:
            console.print(f"[green]✓ Already authenticated[/green]  [dim]{token.account_id}[/dim]")
            console.print("[dim]Use --force to re-authenticate[/dim]")
            return

    console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
    token = login_oauth_interactive(
        print_fn=_oauth_print,
        prompt_fn=lambda s: typer.prompt(s),
        originator="roborsi",
    )
    if not (token and token.access):
        console.print("[red]✗ Authentication failed[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")


@_register_login("github_copilot")
def _login_github_copilot(force: bool = False) -> None:
    # GitHub Copilot uses device flow via LiteLLM — no local token cache to check
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
