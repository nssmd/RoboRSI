from __future__ import annotations

from roborsi.embodied.embodiment.arm.flexiv.protocol import (
    Request,
    Response,
    decode_request,
    decode_response,
)


def test_request_roundtrip():
    req = Request(action="move_joint", params={"q": [0.1, 0.2, 0.3]})
    decoded = decode_request(req.encode())
    assert decoded.action == "move_joint"
    assert decoded.params == {"q": [0.1, 0.2, 0.3]}
    assert decoded.id == req.id


def test_response_ok_roundtrip():
    resp = Response.ok_("rid-1", {"x": 1})
    parsed = decode_response(resp.encode())
    assert parsed.id == "rid-1"
    assert parsed.ok is True
    assert parsed.data == {"x": 1}


def test_response_error_roundtrip():
    resp = Response.err_("rid-2", "bad", code="invalid_params")
    parsed = decode_response(resp.encode())
    assert parsed.id == "rid-2"
    assert parsed.ok is False
    assert parsed.error == "bad"
    assert parsed.code == "invalid_params"


def test_encode_terminates_with_newline():
    assert Request(action="ping").encode().endswith(b"\n")
    assert Response.ok_("rid", {}).encode().endswith(b"\n")
