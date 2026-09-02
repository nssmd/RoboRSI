"""Sidecar asyncio server — owns a single RdkAdapter, serves JSON over a Unix socket.

Launched as::

    python -m roborsi.embodied.embodiment.arm.flexiv.session \\
        --sock /path/to.sock --sn <controller-sn> [--pid /path/to.pid]

Actions are serialized (one mode change at a time) to avoid Flexiv mode
races. ``state`` reads are also serialized — cheap compared to TCP
round-trip, simpler than adding a reader-writer lock.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from loguru import logger

from roborsi.embodied.embodiment.arm.flexiv.protocol import (
    Request,
    Response,
    decode_request,
)
from roborsi.embodied.embodiment.arm.flexiv.session.handlers import HANDLERS
from roborsi.embodied.embodiment.arm.flexiv.session.rdk_adapter import RdkAdapter


class SidecarServer:
    """Single-robot session server."""

    def __init__(self, sock_path: Path, sn: str, pid_path: Path | None = None) -> None:
        self._sock_path = sock_path
        self._sn = sn
        self._pid_path = pid_path
        self._adapter: RdkAdapter | None = None
        self._request_lock = asyncio.Lock()
        self._server: asyncio.base_events.Server | None = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        logger.info(f"Flexiv sidecar starting, sn={self._sn} sock={self._sock_path}")
        self._adapter = RdkAdapter(self._sn)
        if self._pid_path is not None:
            self._pid_path.write_text(str(os.getpid()), encoding="utf-8")
        if self._sock_path.exists():
            self._sock_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self._sock_path))
        logger.info("Flexiv sidecar ready")

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            serve_task = asyncio.create_task(self._server.serve_forever())
            done, pending = await asyncio.wait(
                {shutdown_task, serve_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername") or "client"
        logger.debug(f"Flexiv sidecar accepted {addr}")
        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            await self._handle_line(line, writer)
        writer.close()

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            req = decode_request(line)
        except Exception as exc:
            writer.write(Response.err_("", f"bad request: {exc}", "bad_request").encode())
            await writer.drain()
            return

        if req.action == "shutdown":
            writer.write(Response.ok_(req.id, {"message": "shutting down"}).encode())
            await writer.drain()
            self._shutdown.set()
            return

        if req.action == "ping":
            writer.write(Response.ok_(req.id, {"pong": True, "sn": self._sn}).encode())
            await writer.drain()
            return

        response = await self._dispatch(req)
        writer.write(response.encode())
        await writer.drain()

    async def _dispatch(self, req: Request) -> Response:
        handler = HANDLERS.get(req.action)
        if handler is None:
            return Response.err_(req.id, f"unknown action: {req.action}", "unknown_action")
        assert self._adapter is not None
        async with self._request_lock:
            try:
                data = await handler(self._adapter, req.params)
            except ValueError as exc:
                return Response.err_(req.id, str(exc), "invalid_params")
            except TimeoutError as exc:
                return Response.err_(req.id, str(exc), "timeout")
        return Response.ok_(req.id, data)

    async def stop(self) -> None:
        logger.info("Flexiv sidecar stopping")
        if self._adapter is not None:
            self._adapter.shutdown()
        if self._sock_path.exists():
            self._sock_path.unlink()
        if self._pid_path is not None and self._pid_path.exists():
            self._pid_path.unlink()


def _install_signal_handlers(server: SidecarServer, loop: asyncio.AbstractEventLoop) -> None:
    def _request_shutdown() -> None:
        server._shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)


async def _main(args: argparse.Namespace) -> int:
    server = SidecarServer(Path(args.sock), args.sn, Path(args.pid) if args.pid else None)
    loop = asyncio.get_running_loop()
    _install_signal_handlers(server, loop)
    await server.start()
    try:
        await server.serve_forever()
    finally:
        await server.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Flexiv sidecar session")
    parser.add_argument("--sock", required=True, help="Unix socket path")
    parser.add_argument("--sn", required=True, help="Flexiv controller serial number")
    parser.add_argument("--pid", required=False, help="Optional pid file path")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
