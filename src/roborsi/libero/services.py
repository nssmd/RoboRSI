"""Lifecycle management for the isolated PyRoKi solver service."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ServiceStatus:
    running: bool
    pid: int | None
    port: int
    detail: str


def _state_path(repo_root: Path) -> Path:
    return repo_root / ".runtime/services.json"


def _load_state(repo_root: Path) -> dict:
    path = _state_path(repo_root)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(repo_root: Path, state: dict) -> None:
    path = _state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def pyroki_command(repo_root: Path, *, port: int) -> tuple[list[str], dict[str, str]]:
    root = Path(repo_root).resolve()
    command = [
        str(root / ".venv-pyroki/bin/python"),
        str(root / "scripts/pyroki_ik_server.py"),
    ]
    env = os.environ.copy()
    env["ROBORSI_PYROKI_PORT"] = str(port)
    return command, env


def service_status(repo_root: Path, *, default_port: int = 5559) -> ServiceStatus:
    root = Path(repo_root).resolve()
    row = (_load_state(root).get("pyroki") or {})
    pid = int(row.get("pid") or 0) or None
    port = int(row.get("port") or default_port)
    alive = _pid_alive(pid)
    ready = _port_ready(port)
    if alive and ready:
        return ServiceStatus(True, pid, port, "ready")
    if alive:
        return ServiceStatus(False, pid, port, "process is warming or unavailable")
    return ServiceStatus(False, pid, port, "stopped")


def start_service(repo_root: Path, *, port: int = 5559, timeout_s: float = 180.0) -> ServiceStatus:
    root = Path(repo_root).resolve()
    current = service_status(root, default_port=port)
    if current.running:
        return current
    command, env = pyroki_command(root, port=port)
    for required in (Path(command[0]), Path(command[1])):
        if not required.is_file():
            raise FileNotFoundError(f"required service file missing: {required}; run ./setup.sh")
    log_path = root / ".runtime/pyroki.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab")
    process = subprocess.Popen(
        command,
        cwd=root / "scripts",
        env=env,
        stdout=stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    stream.close()
    state = _load_state(root)
    state["pyroki"] = {
        "pid": process.pid,
        "port": port,
        "status": "starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log": str(log_path.relative_to(root)),
    }
    _write_state(root, state)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            state["pyroki"]["status"] = "failed"
            state["pyroki"]["returncode"] = process.returncode
            _write_state(root, state)
            raise RuntimeError(
                f"PyRoKi service exited with code {process.returncode}; see {log_path}"
            )
        if _port_ready(port):
            state["pyroki"]["status"] = "running"
            state["pyroki"]["ready_at"] = datetime.now(timezone.utc).isoformat()
            _write_state(root, state)
            return ServiceStatus(True, process.pid, port, "ready")
        time.sleep(0.5)
    state["pyroki"]["status"] = "warming"
    _write_state(root, state)
    return ServiceStatus(False, process.pid, port, "still warming; inspect .runtime/pyroki.log")


def stop_service(repo_root: Path) -> ServiceStatus:
    root = Path(repo_root).resolve()
    state = _load_state(root)
    row = state.get("pyroki") or {}
    pid = int(row.get("pid") or 0) or None
    port = int(row.get("port") or 5559)
    if _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10.0
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
    row["status"] = "stopped"
    row["stopped_at"] = datetime.now(timezone.utc).isoformat()
    state["pyroki"] = row
    _write_state(root, state)
    return ServiceStatus(False, pid, port, "stopped")
