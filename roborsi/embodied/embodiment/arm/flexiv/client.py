"""FlexivClient — synchronous socket client shared by CLI and LLM tool.

One line-delimited JSON request per call. Responses are matched by the
request id to avoid cross-talk if the caller later reuses a connection.

For MVP: each ``call()`` opens a fresh connection. Keeping it simple; a
long-lived connection pool can come later.
"""

from __future__ import annotations

import socket
from typing import Any

from roborsi.embodied.embodiment.arm.flexiv.lifecycle import (
    SessionPaths,
    sidecar_running,
)
from roborsi.embodied.embodiment.arm.flexiv.protocol import (
    Request,
    decode_response,
)


class FlexivClientError(RuntimeError):
    """Raised when the sidecar returns an error response."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class FlexivNotConnected(FlexivClientError):
    """Raised when the sidecar for the alias is not running."""

    def __init__(self, alias: str):
        super().__init__(
            f"Flexiv sidecar for '{alias}' is not running. "
            f"Start it with: roborsi flexiv connect --alias {alias}",
            code="not_connected",
        )


class FlexivClient:
    """Blocking JSON-over-unix-socket client."""

    def __init__(self, alias: str) -> None:
        self._alias = alias
        self._paths = SessionPaths.for_alias(alias)

    @property
    def alias(self) -> str:
        return self._alias

    @property
    def paths(self) -> SessionPaths:
        return self._paths

    def is_connected(self) -> bool:
        return sidecar_running(self._paths)

    def call(
        self, action: str, params: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        if not self.is_connected():
            raise FlexivNotConnected(self._alias)
        req = Request(action=action, params=params or {})
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self._paths.sock))
            sock.sendall(req.encode())
            line = _read_line(sock, timeout)
        finally:
            sock.close()
        resp = decode_response(line)
        if resp.id != req.id:
            raise FlexivClientError(
                f"response id mismatch: expected {req.id}, got {resp.id}", code="id_mismatch",
            )
        if not resp.ok:
            raise FlexivClientError(resp.error or "sidecar error", code=resp.code or "error")
        return resp.data or {}


def _read_line(sock: socket.socket, timeout: float) -> bytes:
    """Read bytes from *sock* until a newline. Raises on EOF without newline."""
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    buf = b"".join(chunks)
    if b"\n" not in buf:
        raise FlexivClientError("sidecar closed connection before responding", code="eof")
    line, _, _ = buf.partition(b"\n")
    return line
