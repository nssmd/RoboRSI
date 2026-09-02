"""``roborsi camera`` — Typer subapp for camera capture.

Single surface for humans (interactive) and agents (``--json``). All
backends share one alias-based registry; ``snapshot`` dispatches by
``binding.backend``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.embodied.embodiment.camera import (
    CameraRegistry,
    all_backends,
    assert_backend,
)


camera_app = typer.Typer(name="camera", help="Camera capture.", no_args_is_help=True)
console = Console()


def _emit(data: Any, as_json: bool, human: str | None = None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return
    if human:
        console.print(human)
    else:
        console.print_json(data=data)


def _fail(message: str, as_json: bool, code: str = "error") -> None:
    if as_json:
        sys.stdout.write(json.dumps({"ok": False, "error": message, "code": code}) + "\n")
        sys.stdout.flush()
    else:
        console.print(f"[red]✗[/red] {message}")
    raise typer.Exit(1)


# ── discover ─────────────────────────────────────────────────────────────


@camera_app.command("discover")
def discover(
    backend: str = typer.Option("iphone", "--backend", help=f"One of {list(all_backends())}"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List cameras the OS can currently see for *backend*."""
    assert_backend(backend)
    if backend == "iphone":
        from roborsi.embodied.embodiment.camera.iphone import list_devices
        devices = [d.to_dict() for d in list_devices()]
    else:  # pragma: no cover - guarded by assert_backend
        devices = []
    if as_json:
        _emit({"backend": backend, "devices": devices}, as_json=True)
        return
    if not devices:
        console.print(
            f"[dim]No {backend} devices detected. "
            "For iPhone: plug in via USB, open Record3D, and trust this computer.[/dim]"
        )
        return
    table = Table(title=f"Detected {backend} devices")
    table.add_column("UDID", style="cyan")
    table.add_column("Product ID", style="magenta")
    for d in devices:
        table.add_row(str(d.get("udid", "")), str(d.get("product_id", "")))
    console.print(table)


# ── add / list / remove ──────────────────────────────────────────────────


@camera_app.command("add")
def add(
    alias: str = typer.Option(..., "--alias", "-a", help="Short name to identify the camera."),
    backend: str = typer.Option("iphone", "--backend", help=f"One of {list(all_backends())}"),
    udid: str = typer.Option("", "--udid", help="Device UDID (empty = first available)."),
    notes: str = typer.Option("", "--notes", help="Free-form notes (mount, lens...)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Register a camera under *alias*."""
    binding = CameraRegistry().add(alias=alias, backend=backend, udid=udid, notes=notes)
    _emit(
        {"ok": True, "binding": binding.to_dict()},
        as_json,
        human=f"[green]✓[/green] Registered camera '{alias}' ({backend}, udid={udid or 'auto'}).",
    )


@camera_app.command("list")
def list_(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List all registered cameras."""
    bindings = [b.to_dict() for b in CameraRegistry().list()]
    if as_json:
        _emit({"cameras": bindings}, as_json=True)
        return
    if not bindings:
        console.print("[dim]No cameras registered. Use 'roborsi camera add'.[/dim]")
        return
    table = Table(title="Registered Cameras")
    table.add_column("Alias", style="cyan")
    table.add_column("Backend", style="green")
    table.add_column("UDID", style="magenta")
    table.add_column("Notes", style="white")
    for b in bindings:
        table.add_row(b["alias"], b["backend"], b.get("udid", ""), b.get("notes", ""))
    console.print(table)


@camera_app.command("remove")
def remove(
    alias: str = typer.Option(..., "--alias", "-a"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Unregister a camera."""
    CameraRegistry().remove(alias)
    _emit(
        {"ok": True, "alias": alias},
        as_json,
        human=f"[green]✓[/green] Removed '{alias}'.",
    )


# ── snapshot ─────────────────────────────────────────────────────────────


@camera_app.command("snapshot")
def snapshot(
    alias: str = typer.Option(..., "--alias", "-a"),
    out: Path = typer.Option(..., "--out", help="Destination image path (.jpg/.png)."),
    timeout: float = typer.Option(10.0, "--timeout", help="Max seconds to wait for first frame."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Capture one RGB frame from *alias* and save it."""
    binding = CameraRegistry().get(alias)
    if binding is None:
        _fail(f"Unknown camera alias '{alias}'. Run 'camera list'.", as_json, code="unknown_alias")

    if binding.backend != "iphone":
        _fail(f"Backend '{binding.backend}' not implemented yet.", as_json, code="not_implemented")

    from roborsi.embodied.embodiment.camera.iphone import IPhoneSession, IPhoneSnapshotError

    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    session = IPhoneSession(udid=binding.udid, rgb_timeout=timeout)
    try:
        with session:
            rgb = session.snapshot()
            connected_udid = session.connected_udid
            product_id = session.connected_product_id
    except IPhoneSnapshotError as exc:
        _fail(str(exc), as_json, code="capture_failed")

    import cv2  # local import — heavy dep already present
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(out), bgr):
        _fail(f"cv2.imwrite failed for {out}", as_json, code="write_failed")
    # Force fsync so the JPEG is on disk before we fork off.
    import os
    try:
        fd = os.open(str(out), os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass

    elapsed_ms = int((time.monotonic() - started) * 1000)
    height, width = rgb.shape[:2]
    payload = {
        "ok": True,
        "alias": alias,
        "backend": binding.backend,
        "udid": connected_udid,
        "product_id": product_id,
        "path": str(out),
        "width": int(width),
        "height": int(height),
        "elapsed_ms": elapsed_ms,
    }
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        console.print(f"[green]✓[/green] Saved {out} ({width}×{height}, {elapsed_ms} ms).")
