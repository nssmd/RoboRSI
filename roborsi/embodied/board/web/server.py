"""Unified board web server: evo dashboard (:8787) + session cockpit (:8795).

``serve()`` runs both FastAPI apps in ONE process via ``asyncio.gather`` over two
uvicorn Servers — the "server.py -> :8787 + :8795" shape. Passing
``evo_port=None`` or ``cockpit_port=None`` runs just one app; the thin launchers
``scripts/evo_dashboard.py`` / ``scripts/session_cockpit.py`` do exactly that so
the standing cron can restart each port independently. A port already bound is
skipped with a log line instead of crashing (bind-or-skip), so the two launchers
coexist safely.
"""
from __future__ import annotations

import argparse
import asyncio
import socket

DEFAULT_EVO_PORT = 8787
DEFAULT_COCKPIT_PORT = 8795


def _port_free(host: str, port: int) -> bool:
    """True if we can bind (host, port) right now — an active listener fails to
    rebind even with SO_REUSEADDR, so this detects a running server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _uvicorn_server(app, host: str, port: int):
    import uvicorn
    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))


async def _serve_apps(host: str, evo_port: int | None,
                      cockpit_port: int | None, auth_token: str | None) -> None:
    servers = []
    if evo_port and _port_free(host, evo_port):
        from .evo_app import create_app
        servers.append(_uvicorn_server(create_app(), host, evo_port))
    elif evo_port:
        print(f"[board.web] :{evo_port} already bound — skip evo app", flush=True)
    if cockpit_port and _port_free(host, cockpit_port):
        from .cockpit_app import create_app
        servers.append(_uvicorn_server(create_app(auth_token=auth_token), host, cockpit_port))
    elif cockpit_port:
        print(f"[board.web] :{cockpit_port} already bound — skip cockpit app", flush=True)
    if not servers:
        print("[board.web] nothing to serve (all requested ports bound)", flush=True)
        return
    _install_group_shutdown(servers)
    await asyncio.gather(*(s.serve() for s in servers))


def _install_group_shutdown(servers) -> None:
    """Drive a single SIGINT/SIGTERM handler that stops EVERY server.

    uvicorn's per-server ``capture_signals`` installs a process-wide handler
    bound to one instance, so with two gathered servers only the last-registered
    one would exit on a signal and ``gather`` would hang. Disable each server's
    own capture and set ``should_exit`` on all of them from one loop handler."""
    import signal
    for s in servers:
        s.install_signal_handlers = lambda: None   # neutralise uvicorn's own capture

    def _stop() -> None:
        for s in servers:
            s.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:               # e.g. non-Unix event loops
            pass


def serve(*, host: str = "0.0.0.0", evo_port: int | None = DEFAULT_EVO_PORT,
          cockpit_port: int | None = DEFAULT_COCKPIT_PORT,
          auth_token: str | None = None) -> int:
    """Run the requested board web apps (blocking). Defaults to both ports."""
    asyncio.run(_serve_apps(host, evo_port, cockpit_port, auth_token))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Unified board web dashboards (evo :8787 + cockpit :8795)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--evo-port", type=int, default=DEFAULT_EVO_PORT)
    ap.add_argument("--cockpit-port", type=int, default=DEFAULT_COCKPIT_PORT)
    ap.add_argument("--token", default=None)
    a = ap.parse_args()
    serve(host=a.host, evo_port=a.evo_port, cockpit_port=a.cockpit_port,
          auth_token=a.token)


if __name__ == "__main__":
    main()
