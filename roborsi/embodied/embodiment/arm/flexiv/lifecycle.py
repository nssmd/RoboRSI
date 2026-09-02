"""Lifecycle management — spawn / detect / kill Flexiv sidecar processes.

State lives on disk under ``~/.roborsi/flexiv/<alias>.{sock,pid,log}``::

    .sock   unix socket the sidecar listens on
    .pid    integer pid, written by the sidecar at startup
    .log    stdout + stderr captured while the sidecar runs

Tools here are synchronous — they only handle process ctl, not I/O.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from roborsi.embodied.embodiment.arm.flexiv.manifest_slot import get_flexiv_runtime_dir


@dataclass(frozen=True)
class SessionPaths:
    alias: str
    sock: Path
    pid: Path
    log: Path

    @classmethod
    def for_alias(cls, alias: str) -> "SessionPaths":
        base = get_flexiv_runtime_dir()
        return cls(
            alias=alias,
            sock=base / f"{alias}.sock",
            pid=base / f"{alias}.pid",
            log=base / f"{alias}.log",
        )


def read_pid(paths: SessionPaths) -> int | None:
    if not paths.pid.exists():
        return None
    raw = paths.pid.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return int(raw)


def is_alive(pid: int) -> bool:
    """Posix alive-check via kill(pid, 0)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sidecar_running(paths: SessionPaths) -> bool:
    pid = read_pid(paths)
    return pid is not None and is_alive(pid)


def spawn_sidecar(alias: str, sn: str) -> SessionPaths:
    """Launch the sidecar detached and return its ``SessionPaths``."""
    paths = SessionPaths.for_alias(alias)
    if sidecar_running(paths):
        raise RuntimeError(
            f"Flexiv sidecar for '{alias}' is already running (pid={read_pid(paths)})"
        )

    # Clean stale socket / pid from a prior crash.
    for stale in (paths.sock, paths.pid):
        if stale.exists():
            stale.unlink()

    argv = [
        sys.executable,
        "-m",
        "roborsi.embodied.embodiment.arm.flexiv.session",
        "--sock", str(paths.sock),
        "--sn", sn,
        "--pid", str(paths.pid),
    ]
    log_fd = paths.log.open("ab")
    subprocess.Popen(
        argv,
        stdout=log_fd,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=_inherit_env(),
    )
    _wait_for_socket(paths.sock, timeout=5.0)
    return paths


def _inherit_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _wait_for_socket(sock_path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not sock_path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError(f"Sidecar did not create socket at {sock_path} within {timeout}s")
        time.sleep(0.05)


def terminate_sidecar(paths: SessionPaths, grace: float = 3.0) -> bool:
    """Send SIGTERM, wait up to *grace* seconds, escalate to SIGKILL.

    Returns True if a process was stopped, False if nothing was running.
    """
    pid = read_pid(paths)
    if pid is None or not is_alive(pid):
        _cleanup_files(paths)
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while is_alive(pid):
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            break
        time.sleep(0.05)
    _cleanup_files(paths)
    return True


def _cleanup_files(paths: SessionPaths) -> None:
    for path in (paths.sock, paths.pid):
        if path.exists():
            path.unlink()
