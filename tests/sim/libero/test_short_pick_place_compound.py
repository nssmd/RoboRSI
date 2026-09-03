from __future__ import annotations

from types import SimpleNamespace

from roborsi.embodied.agent_loop.env import Observation
from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
from roborsi.embodied.skills.atomic.libero_pick_place.visual_pick_place import (
    policy,
)
from roborsi.runtime_mode import use_run_mode


def test_visual_pick_place_surface_runs_code_backed_sequence(monkeypatch) -> None:
    calls = []

    def fake_tool(_state, name, args):
        calls.append((name, dict(args)))
        if name == "look":
            return {"ok": True}, Observation()
        if name == "find_by_pointing":
            point = 20 if "bowl" in args["object"] else 40
            return {"ok": True, "u": point, "v": 30}, Observation()
        if name == "grasp_object":
            return {
                "ok": True,
                "grasped": True,
                "holding": True,
                "visual_verified": True,
                "identity_verified": True,
            }, Observation()
        if name == "place_on_surface":
            return {
                "ok": True,
                "released": True,
                "gripper_opened": True,
            }, Observation()
        raise AssertionError(name)

    monkeypatch.setattr(policy, "_tool", fake_tool)
    result, _ = policy.dispatch_runtime(
        SimpleNamespace(),
        {
            "source": "akita black bowl",
            "target": "yellow_plate_1",
            "placement": "surface",
        },
    )

    assert result["ok"] is True
    assert [name for name, _ in calls] == [
        "look",
        "find_by_pointing",
        "grasp_object",
        "find_by_pointing",
        "place_on_surface",
        "look",
    ]
    assert calls[3][1]["object"] == "plate"


def test_visual_pick_place_container_runs_code_backed_sequence(monkeypatch) -> None:
    calls = []

    def fake_tool(_state, name, args):
        calls.append((name, dict(args)))
        if name == "look":
            return {"ok": True}, Observation()
        if name == "find_by_pointing":
            point = 20 if "cream" in args["object"] else 40
            return {"ok": True, "u": point, "v": 30}, Observation()
        if name == "grasp_object":
            return {
                "ok": True,
                "grasped": True,
                "holding": True,
                "visual_verified": True,
                "identity_verified": True,
            }, Observation()
        if name == "place_object_in":
            return {
                "ok": True,
                "released": True,
                "gripper_opened": True,
            }, Observation()
        raise AssertionError(name)

    monkeypatch.setattr(policy, "_tool", fake_tool)
    result, _ = policy.dispatch_runtime(
        SimpleNamespace(),
        {
            "source": "cream cheese",
            "target": "wooden_bowl_1",
            "placement": "container",
            "z_offset": 0.05,
        },
    )

    assert result["ok"] is True
    assert calls[3][1]["object"] == "bowl"
    assert calls[4] == (
        "place_object_in",
        {"object": "bowl", "pixel": [40, 30], "z_offset": 0.05},
    )


def test_visual_pick_place_stops_on_unverified_identity(monkeypatch) -> None:
    calls = []

    def fake_tool(_state, name, _args):
        calls.append(name)
        if name == "look":
            return {"ok": True}, Observation()
        if name == "find_by_pointing":
            return {"ok": True, "u": 20, "v": 30}, Observation()
        if name == "grasp_object":
            return {
                "ok": True,
                "grasped": True,
                "holding": True,
                "visual_verified": True,
                "identity_verified": False,
                "do_not_regrasp": True,
            }, Observation()
        raise AssertionError(name)

    monkeypatch.setattr(policy, "_tool", fake_tool)
    result, _ = policy.dispatch_runtime(
        SimpleNamespace(),
        {"source": "black bowl", "target": "plate", "placement": "surface"},
    )

    assert result["ok"] is False
    assert result["failed_phase"] == "grasp_identity"
    assert "place_on_surface" not in calls


def test_released_compound_is_visible_in_frozen_eval_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ROBORSI_ATOMIC_COMPOUND", raising=False)
    with use_run_mode("eval"):
        names = {
            row["function"]["name"]
            for row in _build_tool_specs(ns="libero", task="libero_pick_place")
        }
    assert "visual_pick_place" in names


def test_released_compound_can_be_disabled_for_code_off(monkeypatch) -> None:
    monkeypatch.setenv("ROBORSI_ATOMIC_COMPOUND", "0")
    names = {
        row["function"]["name"]
        for row in _build_tool_specs(ns="libero", task="libero_pick_place")
    }
    assert "visual_pick_place" not in names
