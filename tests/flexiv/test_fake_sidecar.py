"""End-to-end: spawn a real sidecar (using fake RDK) and drive it via FlexivClient."""

from __future__ import annotations

import time

import pytest

from roborsi.embodied.embodiment.arm.flexiv.client import FlexivClient
from roborsi.embodied.embodiment.arm.flexiv.lifecycle import (
    SessionPaths,
    sidecar_running,
    spawn_sidecar,
    terminate_sidecar,
)
from roborsi.embodied.embodiment.arm.flexiv.manifest_slot import FlexivRegistry


@pytest.fixture
def connected(tmp_path):
    FlexivRegistry().add("e2e", "Rizon4-E2E", "Rizon4")
    paths = spawn_sidecar("e2e", "Rizon4-E2E")
    # sidecar is running; make sure it fully opens the socket
    for _ in range(50):
        if sidecar_running(paths):
            break
        time.sleep(0.05)
    yield FlexivClient("e2e")
    terminate_sidecar(paths)


def test_ping_roundtrip(connected):
    pong = connected.call("ping", {})
    assert pong["pong"] is True
    assert pong["sn"] == "Rizon4-E2E"


def test_state_returns_seven_joints(connected):
    state = connected.call("state", {})
    assert len(state["q"]) == 7
    assert state["mode"] == "IDLE"


def test_move_joint_updates_state(connected):
    target = [0.1, -0.2, 0.3, 0.4, -0.1, 0.2, 0.05]
    connected.call("move_joint", {"q": target, "vel": 0.5})
    state = connected.call("state", {})
    assert state["q"] == target
    assert state["mode"] == "NRT_JOINT_POSITION"


def test_gripper_width(connected):
    result = connected.call("gripper_width", {"value": 0.04})
    assert result["width"] == pytest.approx(0.04)


def test_unknown_action_errors(connected):
    from roborsi.embodied.embodiment.arm.flexiv.client import FlexivClientError
    with pytest.raises(FlexivClientError) as excinfo:
        connected.call("does_not_exist", {})
    assert excinfo.value.code == "unknown_action"


def test_shutdown_stops_sidecar(connected):
    connected.call("shutdown", {})
    # give the sidecar a moment to exit
    paths = SessionPaths.for_alias("e2e")
    for _ in range(40):
        if not sidecar_running(paths):
            break
        time.sleep(0.05)
    assert not sidecar_running(paths)
