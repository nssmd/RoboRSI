"""Smoke tests for the ``roborsi camera`` Typer subapp."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from roborsi.cli.camera import camera_app


runner = CliRunner()


def test_discover_iphone_lists_fake_device():
    result = runner.invoke(camera_app, ["discover", "--backend", "iphone", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["backend"] == "iphone"
    udids = [d["udid"] for d in payload["devices"]]
    assert "FAKE-UDID-0001" in udids


def test_add_list_remove_roundtrip():
    add = runner.invoke(
        camera_app,
        ["add", "--alias", "front", "--backend", "iphone", "--json"],
    )
    assert add.exit_code == 0, add.output

    listed = runner.invoke(camera_app, ["list", "--json"])
    payload = json.loads(listed.stdout.strip().splitlines()[-1])
    assert [c["alias"] for c in payload["cameras"]] == ["front"]

    rm = runner.invoke(camera_app, ["remove", "--alias", "front", "--json"])
    assert rm.exit_code == 0, rm.output

    listed_after = runner.invoke(camera_app, ["list", "--json"])
    payload_after = json.loads(listed_after.stdout.strip().splitlines()[-1])
    assert payload_after["cameras"] == []


def test_snapshot_command_writes_jpg(tmp_path: Path):
    runner.invoke(camera_app, ["add", "--alias", "wrist", "--backend", "iphone", "--json"])
    out = tmp_path / "shot.jpg"
    result = runner.invoke(
        camera_app,
        ["snapshot", "--alias", "wrist", "--out", str(out), "--timeout", "2", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["width"] == 640 and payload["height"] == 480
    assert out.exists() and out.stat().st_size > 0


def test_snapshot_unknown_alias_fails():
    out_arg = "/tmp/should-not-be-written.jpg"
    result = runner.invoke(
        camera_app,
        ["snapshot", "--alias", "ghost", "--out", out_arg, "--json"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["code"] == "unknown_alias"
