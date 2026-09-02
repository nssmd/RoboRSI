"""Trace-introspection helpers for atomic post-episode structural gates.

Used by .zeroshot policies to override VLM-declared success when the
trace shows the VLM never actually executed the required primitive
(e.g. `pick_actor_by_contact_point`) or never released the gripper.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


def _iter_calls(trace: list[dict] | None) -> Iterable[tuple[str, dict]]:
    """Yield (tool_name, args) from any trace shape we encounter:
      - rollout:   step = {"tool_call": {"tool": NAME, "args": ARGS}, "result": ...}
      - codeact:   step = {"tool_calls": [{"name": NAME, "args"|"input": ARGS}]}
      - flat:      step = {"tool": NAME, "args"|"input": ARGS}"""
    for step in (trace or []):
        tc = step.get("tool_call")
        if isinstance(tc, dict):
            yield tc.get("tool", "") or tc.get("name", ""), (tc.get("args") or tc.get("input") or {})
        for call in (step.get("tool_calls") or []):
            yield call.get("name", "") or call.get("tool", ""), (call.get("args") or call.get("input") or {})
        if step.get("tool"):
            yield step["tool"], (step.get("args") or step.get("input") or {})


def trace_invoked(trace: list[dict] | None,
                    tool_names: Iterable[str] | str) -> bool:
    """True if any call in `trace` matches a name in `tool_names`.

    Accepts either a single string OR an iterable of strings. (Without the
    isinstance check, `set("foo")` would split into characters and silently
    never match — a silent gate-broken bug we shipped twice.)"""
    if isinstance(tool_names, str):
        names = {tool_names}
    else:
        names = set(tool_names)
    return any(n in names for n, _ in _iter_calls(trace))


def trace_invoked_with_args(trace: list[dict] | None, name: str,
                              arg_pred: Callable[[dict[str, Any]], bool]) -> bool:
    """True if any call named `name` has args satisfying `arg_pred`."""
    return any(n == name and arg_pred(a) for n, a in _iter_calls(trace))


def trace_done_success(trace: list[dict] | None) -> bool:
    """True if a `done(success=True)` call appears anywhere in trace."""
    return trace_invoked_with_args(trace, "done", lambda a: bool(a.get("success")))


def trace_invoked_returning(trace: list[dict] | None, tool_name: str,
                              result_pred: Callable[[dict[str, Any]], bool]) -> bool:
    """True if `tool_name` was called AND its RESULT dict satisfies
    `result_pred`. Rollout trace stores result under step.result.

    Use this when a wrapper skill (e.g. pick_actor_by_contact_point)
    internally calls verify_holding_visual — the outer atomic's gate
    only sees the wrapper's result, not the inner verify. Inspecting
    the wrapper's result.holding_visual gives the gate the same signal
    that VLM-direct verify_holding_visual would have."""
    for step in (trace or []):
        tc = step.get("tool_call")
        if isinstance(tc, dict) and tc.get("tool") == tool_name:
            res = step.get("result") or {}
            if isinstance(res, dict) and result_pred(res):
                return True
    return False


def trace_verified_holding(trace: list[dict] | None, arm: str,
                             min_confidence: float = 0.7) -> bool:
    """True if any verify_holding_visual call returned holding_visual=True
    with confidence >= threshold for the given arm.

    Used by atomic gates as the canonical bypass: regardless of which
    motion primitive the VLM chose, if it verified visually, the grasp
    happened. Prevents the gate from firing on non-canonical-but-correct
    paths (e.g. move_fingertip_to + set_gripper instead of pick_*_v2).

    Rollout trace shape: each step has {tool_call: {tool, args}, result: {...}}."""
    for step in (trace or []):
        tc = step.get("tool_call") or {}
        if tc.get("tool") != "verify_holding_visual":
            continue
        if (tc.get("args") or {}).get("arm") != arm:
            continue
        res = step.get("result") or {}
        if (res.get("holding_visual")
                and float(res.get("confidence") or 0) >= min_confidence):
            return True
    return False
