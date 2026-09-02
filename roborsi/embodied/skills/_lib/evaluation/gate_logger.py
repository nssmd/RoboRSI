"""Append-only structured log of gate decisions. Used by atomic .zeroshot
policies to record every structural-gate override, and by the LH triangle
(LHExecutor) to record gate-vs-judge contradictions.

Why: gate bugs are silent (always returns False) and cascade hard. A
dedicated log file makes misfires visible without parsing scattered orchestrator
logs. Read with `tail -F ~/.roborsi/gate_log.jsonl`.

Line schema (one JSON object per line):
    {ts: ISO timestamp, kind: "fire" | "contradiction" | "pass",
     atomic: <name>, gate_outcome: <str>, judge_success: <bool|None>,
     primitives_seen: [<tool_name>...], trace_steps: <int>,
     extra: {...freeform...}}

Detection of contradiction (gate=fail BUT judge=success) is the strongest
indicator that the gate's allowlist or shape-matching is broken — this is
the failure mode that wasted V8 (commit c53d6e5).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable


_LOG_PATH = Path.home() / ".roborsi" / "gate_log.jsonl"


def _primitives_seen(trace: list[dict] | None) -> list[str]:
    """Distinct tool names that appear in trace, in order of first appearance."""
    from .trace_inspect import _iter_calls
    seen: list[str] = []
    for n, _ in _iter_calls(trace):
        if n and n not in seen:
            seen.append(n)
    return seen


def log_gate_fire(atomic: str, gate_outcome: str, trace: list[dict] | None,
                   extra: dict[str, Any] | None = None) -> None:
    """Record that a structural gate overrode VLM success to failure.
    Call from inside the atomic's .zeroshot policy after setting
    rollout.outcome."""
    _append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": "fire",
        "atomic": atomic,
        "gate_outcome": gate_outcome,
        "judge_success": None,
        "primitives_seen": _primitives_seen(trace),
        "trace_steps": len(trace or []),
        "extra": extra or {},
    })


def log_contradiction(atomic: str, gate_outcome: str, judge_success: bool,
                        primitives_seen: Iterable[str] | None = None,
                        judge_reason: str | None = None) -> None:
    """Record gate-vs-judge disagreement. Called from the LH triangle
    (LHExecutor) after both signals are known.

    gate=fail + judge=True is the canonical sign of a buggy gate (e.g.
    allowlist missing a primitive, trace-shape mismatch). gate=pass +
    judge=False is a buggy gate too (let through a fake success) but
    less common.

    Always print a WARNING to stderr in addition to logging."""
    msg = (f"[gate-contradiction] {atomic}: gate_outcome={gate_outcome!r} "
            f"judge_success={judge_success} primitives={list(primitives_seen or [])}"
            f" reason={judge_reason!r}")
    print(msg, flush=True)
    _append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": "contradiction",
        "atomic": atomic,
        "gate_outcome": gate_outcome,
        "judge_success": judge_success,
        "judge_reason": (judge_reason or "")[:200],
        "primitives_seen": list(primitives_seen or []),
        "trace_steps": None,
        "extra": {},
    })


def _append(record: dict[str, Any]) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
