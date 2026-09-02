"""Unit tests for trace_inspect. Covers the two silent-False bugs that
shipped to production (2026-05-30 rollout tool_call shape + 2026-06-04
set(str) char-split). Run with: pytest tests/_lib/evaluation/test_trace_inspect.py
"""
from __future__ import annotations

import pytest

from roborsi.embodied.skills._lib.evaluation.trace_inspect import (
    trace_invoked,
    trace_invoked_with_args,
    trace_done_success,
    trace_verified_holding,
)


# ── trace shape fixtures (the THREE we've encountered in production) ──


ROLLOUT_TRACE = [
    {"tool_call": {"tool": "describe_scene_actors", "args": {}},
     "result": {"ok": True, "count": 4}},
    {"tool_call": {"tool": "pick_actor_by_contact_point",
                    "args": {"arm": "right", "actor_name": "cup_2",
                              "contact_point_id": 0}},
     "result": {"ok": True, "success": True, "holding_visual": True,
                 "confidence": 1.0}},
    {"tool_call": {"tool": "verify_holding_visual",
                    "args": {"arm": "right", "object": "silver bowl"}},
     "result": {"ok": True, "holding_visual": True, "confidence": 1.0}},
    {"tool_call": {"tool": "done", "args": {"success": True}},
     "result": {}},
]

CODEACT_TRACE = [
    {"tool_calls": [{"name": "look", "args": {}}]},
    {"tool_calls": [
        {"name": "pick_actor_by_contact_point",
         "args": {"arm": "left", "actor_name": "cup", "contact_point_id": 0}}
    ]},
    {"tool_calls": [{"name": "done", "args": {"success": True}}]},
]

FLAT_TRACE = [
    {"tool": "pick_bowl_lateral_v2",
     "args": {"arm": "right", "x": 0.18, "y": -0.07, "z": 0.78}},
    {"tool": "done", "args": {"success": False}},
]


# ── trace_invoked ──


@pytest.mark.parametrize("trace, label", [
    (ROLLOUT_TRACE, "rollout"),
    (CODEACT_TRACE, "codeact"),
])
def test_trace_invoked_finds_present_tool_via_string(trace, label):
    """REGRESSION 2026-06-04: set('foo') is a character set; without the
    isinstance guard, lookups silently fail. This was the V8 LH bug."""
    assert trace_invoked(trace, "pick_actor_by_contact_point") is True, (
        f"{label} trace must report pick_actor_by_contact_point as invoked")


@pytest.mark.parametrize("trace", [ROLLOUT_TRACE, CODEACT_TRACE, FLAT_TRACE])
def test_trace_invoked_via_list(trace):
    assert trace_invoked(trace, ["nonexistent", "done"]) is True


def test_trace_invoked_negative_string():
    assert trace_invoked(ROLLOUT_TRACE, "never_called_tool") is False


def test_trace_invoked_negative_list():
    assert trace_invoked(ROLLOUT_TRACE, ["a", "b", "c"]) is False


def test_trace_invoked_empty_trace():
    assert trace_invoked(None, "anything") is False
    assert trace_invoked([], "anything") is False


# ── trace_invoked_with_args ──


def test_trace_invoked_with_args_args_predicate():
    assert trace_invoked_with_args(
        ROLLOUT_TRACE, "pick_actor_by_contact_point",
        lambda a: a.get("actor_name") == "cup_2") is True
    assert trace_invoked_with_args(
        ROLLOUT_TRACE, "pick_actor_by_contact_point",
        lambda a: a.get("actor_name") == "cup") is False


# ── trace_done_success ──


def test_trace_done_success_true():
    assert trace_done_success(ROLLOUT_TRACE) is True


def test_trace_done_success_false():
    assert trace_done_success(FLAT_TRACE) is False


# ── trace_verified_holding ──


def test_trace_verified_holding_present():
    """REGRESSION 2026-05-30: helper read step.tool_calls (plural) only;
    rollout's tool_call (singular nested) was invisible, so every
    verify_holding_visual=True went undetected."""
    assert trace_verified_holding(ROLLOUT_TRACE, arm="right") is True


def test_trace_verified_holding_wrong_arm():
    assert trace_verified_holding(ROLLOUT_TRACE, arm="left") is False


def test_trace_verified_holding_low_confidence():
    trace = [{"tool_call": {"tool": "verify_holding_visual",
                              "args": {"arm": "right"}},
                 "result": {"holding_visual": True, "confidence": 0.4}}]
    assert trace_verified_holding(trace, "right", min_confidence=0.7) is False


def test_trace_verified_holding_not_held():
    trace = [{"tool_call": {"tool": "verify_holding_visual",
                              "args": {"arm": "right"}},
                 "result": {"holding_visual": False, "confidence": 0.95}}]
    assert trace_verified_holding(trace, "right") is False


# ── trace_invoked_returning (added 2026-06-04 for wrapper-skill verify) ──


def test_trace_invoked_returning_match():
    """REGRESSION 2026-06-04: V9 LH gate fired unverified_grasp despite
    pick_actor_by_contact_point internally verifying holding_visual=True.
    Need to inspect wrapper's RESULT not just whether VLM called verify."""
    from roborsi.embodied.skills._lib.evaluation.trace_inspect import (
        trace_invoked_returning,
    )
    trace = [{"tool_call": {"tool": "pick_actor_by_contact_point",
                              "args": {"arm": "right"}},
                 "result": {"ok": True, "success": True,
                              "holding_visual": True, "arm": "right"}}]
    assert trace_invoked_returning(
        trace, "pick_actor_by_contact_point",
        lambda r: bool(r.get("holding_visual"))) is True


def test_trace_invoked_returning_predicate_rejects():
    from roborsi.embodied.skills._lib.evaluation.trace_inspect import (
        trace_invoked_returning,
    )
    trace = [{"tool_call": {"tool": "pick_actor_by_contact_point", "args": {}},
                 "result": {"ok": True, "holding_visual": False}}]
    assert trace_invoked_returning(
        trace, "pick_actor_by_contact_point",
        lambda r: bool(r.get("holding_visual"))) is False


def test_trace_invoked_returning_tool_absent():
    from roborsi.embodied.skills._lib.evaluation.trace_inspect import (
        trace_invoked_returning,
    )
    trace = [{"tool_call": {"tool": "look", "args": {}},
                 "result": {"ok": True}}]
    assert trace_invoked_returning(trace, "pick_actor_by_contact_point",
                                      lambda r: True) is False
