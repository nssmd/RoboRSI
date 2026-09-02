"""roborsi.agent.explore — explore-mode infrastructure.

Two responsibilities:
  1. **Load** persisted zeroshot traces from ~/.roborsi/zeroshot_traces/<atomic>/
  2. **Build** RAG-style in-context examples from successful traces, to inject
     into atomic.zeroshot instructions when the atomic is in EXPLORING state.

The lifecycle (from atomic_lifecycle SKILL.md):

    EXPLORING (< N successes):
        instruction = "goal + tool list + 'figure it out'" + top-K past success traces
        Each call costs more tokens but VLM reasons freely.
    DISTILLED (≥ N successes):
        instruction = crystallized recipe (Tier 2 distills from N traces)
        Cheap + stable.
    TRAINED (after atomic spin):
        active_executor = policy:<ckpt>; zeroshot not invoked anymore.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import home as _home


def trace_dir(atomic_name: str) -> Path:
    return _home() / "zeroshot_traces" / atomic_name


def load_traces(atomic_name: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return list of stored zeroshot run records, newest first."""
    d = trace_dir(atomic_name)
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if limit is not None:
        files = files[:limit]
    out: list[dict[str, Any]] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def successful_traces(atomic_name: str, judge_results: dict[str, bool] | None = None,
                       limit: int | None = None) -> list[dict[str, Any]]:
    """Filter to traces flagged as success.

    Accepts either `vlm_declared_success=True` or `atomic_judge_pass=True`.
    The latter handles cases where VLM ran out of budget but the post-hoc
    image judge confirmed the atomic actually succeeded.
    """
    candidates = load_traces(atomic_name)
    out: list[dict[str, Any]] = []
    for rec in candidates:
        if rec.get("vlm_declared_success") or rec.get("atomic_judge_pass"):
            out.append(rec)
        if limit and len(out) >= limit:
            break
    return out


def count_successes(atomic_name: str) -> int:
    return len(successful_traces(atomic_name))


def render_trace_brief(rec: dict[str, Any], max_calls: int = 12) -> str:
    """One-paragraph summary of a single trace for in-context injection."""
    seed = rec.get("seed", "?")
    trace = (rec.get("trace") or [])[:max_calls]
    lines = [f"--- past success (seed={seed}) ---"]
    for s in trace:
        tc = s.get("tool_call") or {}
        args = tc.get("args") or {}
        # Compact arg repr: skip long strings
        compact = {k: (v if not isinstance(v, str) or len(v) < 40 else v[:40] + "…")
                   for k, v in args.items()}
        res = s.get("result") or {}
        ok = res.get("ok")
        lines.append(f"  {s.get('step')}: {tc.get('tool')}({compact}) ok={ok}")
    return "\n".join(lines)


def build_rag_block(atomic_name: str, k: int = 3, max_calls: int = 12) -> str:
    """Top-K successful traces formatted as in-context examples for VLM.

    Returns empty string if no successes yet (caller falls back to
    pure exploration with just goal + tools)."""
    succs = successful_traces(atomic_name, limit=k)
    if not succs:
        return ""
    blocks = [render_trace_brief(rec, max_calls=max_calls) for rec in succs]
    return ("\n\nPast successful runs (use as examples; vary specifics for "
            "the current scene):\n\n" + "\n\n".join(blocks))


def build_explore_instruction(
    atomic_name: str,
    goal: str,
    tools_hint: str = "",
    k: int = 0,
) -> str:
    """Compose an explore-mode instruction.

    explore mode = give the VLM the GOAL + available TOOLS + optional past examples,
    and let it reason freely. No prescribed step-by-step.

    By default (k=0) we do NOT auto-inject past traces. The VLM can call
    `recall_past_success(atomic=..., k=1)` on demand. This keeps prompts short
    so tool_use parsing stays reliable. Pass k>0 to force-inject (legacy).
    """
    parts = [
        f"GOAL: {goal}",
        "",
        "MODE: explore. You have full freedom to compose tool calls. There is "
        "no prescribed recipe. Reason about the scene, choose tools, verify "
        "results, retry on failure. Your tool repertoire is in the system prompt.",
    ]
    if tools_hint:
        parts += ["", f"Hints: {tools_hint}"]
    if k > 0:
        rag = build_rag_block(atomic_name, k=k)
        if rag:
            parts.append(rag)
    parts += [
        "",
        "Hard rules:",
        "  - Always look() before find_pixel / get_object_bbox.",
        "  - For grasps: call is_holding(arm) IMMEDIATELY after grasp; if "
        "holding=false, do NOT declare done(success=true).",
        "  - For releases: call is_holding(arm) after gripper(open); if "
        "holding=true, the release failed.",
        "  - done(success=true) only when the goal is visibly achieved AND "
        "is_holding state matches the goal.",
        f"  - On-demand history: call recall_past_success(atomic='{atomic_name}', k=1) "
        "if you want to see what worked before.",
    ]
    return "\n".join(parts)
