"""LIBERO runtime configuration commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.embodied.sim.libero.runtime import (
    LiberoRuntimeError,
    configure_runtime,
    runtime_status,
)

libero_app = typer.Typer(
    name="libero",
    help="Configure and verify a real LIBERO/LIBERO-PRO runtime.",
    no_args_is_help=True,
)
console = Console()


@libero_app.command("configure")
def configure(
    root: Path = typer.Option(..., "--root", exists=True, file_okay=False),
    initdir: Path | None = typer.Option(
        None,
        "--initdir",
        exists=True,
        file_okay=False,
    ),
    bddldir: Path | None = typer.Option(
        None,
        "--bddldir",
        exists=True,
        file_okay=False,
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Persist the checkout used by both CLI and evaluation workers."""
    try:
        record = configure_runtime(
            root,
            initdir=initdir,
            bddldir=bddldir,
        )
    except LiberoRuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit({"ok": True, **record}, as_json)


@libero_app.command("doctor")
def doctor(
    backend_name: str = typer.Option("libero", "--backend"),
    task: str = typer.Option("libero_object/0", "--task"),
    reset: bool = typer.Option(False, "--reset"),
    seed: int = typer.Option(0, "--seed"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Check imports and optionally create/reset one real simulator task."""
    report = runtime_status()
    if report.get("importable"):
        from roborsi.embodied.agent_loop import get_backend

        backend = get_backend(backend_name)
        available, reason = backend.available()
        report["backend"] = {
            "name": backend_name,
            "available": available,
            "reason": reason,
        }
        if available:
            tasks = backend.list_tasks()
            report["task_count"] = len(tasks)
            report["task_present"] = task in tasks
            if reset:
                if task not in tasks:
                    report["reset"] = {
                        "ok": False,
                        "error": f"task is not exposed by backend: {task}",
                    }
                else:
                    report["reset"] = _reset_smoke(backend, task, seed)
    _emit(report, as_json)
    if not report.get("importable"):
        raise typer.Exit(2)
    backend_report = report.get("backend") or {}
    reset_report = report.get("reset") or {"ok": True}
    if not backend_report.get("available") or not reset_report.get("ok"):
        raise typer.Exit(2)


def _reset_smoke(backend, task: str, seed: int) -> dict[str, Any]:
    try:
        with backend.make_env(task, {"require_depth": True}) as env:
            observation = env.reset(seed)
            return {
                "ok": True,
                "task": task,
                "seed": seed,
                "instruction": str(
                    getattr(env, "instruction", "")
                    or observation.extras.get("instruction", "")
                ),
                "images": sorted(observation.images),
                "state_size": (
                    int(getattr(observation.state, "size", 0))
                    if observation.state is not None
                    else 0
                ),
                "visible_raw_keys": sorted(getattr(env, "raw_obs")()),
            }
    except Exception as exc:
        return {
            "ok": False,
            "task": task,
            "seed": seed,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _emit(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        return
    table = Table(title="LIBERO runtime")
    table.add_column("field")
    table.add_column("value")
    for key in (
        "configured_root",
        "configured_initdir",
        "configured_bddldir",
        "root",
        "commit",
        "importable",
        "task_count",
        "task_present",
        "error",
    ):
        if key in data:
            table.add_row(key, str(data[key]))
    for name, value in (data.get("versions") or {}).items():
        table.add_row(
            f"module/{name}",
            (
                f"{value.get('version')} · {value.get('file')}"
                if value.get("ok")
                else value.get("error", "unavailable")
            ),
        )
    reset = data.get("reset")
    if reset:
        table.add_row("reset", json.dumps(reset, ensure_ascii=False, default=str))
    console.print(table)
