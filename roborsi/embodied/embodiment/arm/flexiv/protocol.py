"""Line-delimited JSON wire protocol between FlexivClient and sidecar."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Request:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def encode(self) -> bytes:
        return (json.dumps({"id": self.id, "action": self.action, "params": self.params}) + "\n").encode("utf-8")


@dataclass
class Response:
    id: str
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""
    code: str = ""
    event: str = ""

    @classmethod
    def ok_(cls, rid: str, data: dict[str, Any] | None = None) -> "Response":
        return cls(id=rid, ok=True, data=data or {})

    @classmethod
    def err_(cls, rid: str, error: str, code: str = "error") -> "Response":
        return cls(id=rid, ok=False, error=error, code=code)

    @classmethod
    def progress(cls, rid: str, data: dict[str, Any]) -> "Response":
        return cls(id=rid, ok=True, data=data, event="progress")

    def encode(self) -> bytes:
        payload: dict[str, Any] = {"id": self.id, "ok": self.ok}
        if self.event:
            payload["event"] = self.event
        if self.ok:
            payload["data"] = self.data or {}
        else:
            payload["error"] = self.error
            payload["code"] = self.code or "error"
        return (json.dumps(payload) + "\n").encode("utf-8")


def decode_request(line: bytes | str) -> Request:
    obj = json.loads(line)
    return Request(id=obj["id"], action=obj["action"], params=obj.get("params", {}))


def decode_response(line: bytes | str) -> Response:
    obj = json.loads(line)
    return Response(
        id=obj["id"],
        ok=bool(obj.get("ok", False)),
        data=obj.get("data"),
        error=obj.get("error", ""),
        code=obj.get("code", ""),
        event=obj.get("event", ""),
    )
