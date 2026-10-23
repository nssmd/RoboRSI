"""Reviewer tools.

The Reviewer decides for itself what to inspect: any step of any round, any
saved camera frame, and the plan and Engineer summary. Every tool reads only
public artifacts from this episode's workspace. Results pass through the
same privileged-state sanitizer as the trace; the simulator verdict is never
stored there before the episode ends.
"""
from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

_MAX_STEPS_PER_CALL = 15
_MAX_TOOL_TURNS = 12


def _round_dirs(workspace_root: Path) -> list[Path]:
    return sorted((workspace_root / "rollout").glob("round_*"))


def _round_trace(round_dir: Path) -> list[dict[str, Any]]:
    from roborsi.agents.roles.reviewer import _sanitize_trace

    for path in round_dir.rglob("trace.json"):
        return _sanitize_trace(json.loads(path.read_text(encoding="utf-8")))
    return []


def _round_frames(round_dir: Path) -> list[Path]:
    return sorted(round_dir.rglob("tick_*.jpg"))


def evidence_index(workspace_root: Path) -> str:
    """Compact table of contents shown to the Reviewer up front."""
    lines = []
    for k, rd in enumerate(_round_dirs(workspace_root), start=1):
        trace = _round_trace(rd)
        tools = [((e.get("tool_call") or {}).get("tool") or "-") for e in trace]
        failed = sum(1 for e in trace
                     if isinstance(e.get("result"), dict)
                     and e["result"].get("ok") is False)
        lines.append(
            f"round {k}: {len(trace)} steps, {failed} failed tool results, "
            f"{len(_round_frames(rd))} camera frames; tools: "
            + ", ".join(tools[:40])
        )
    return "\n".join(lines) or "(no rounds recorded)"


def execution_chain(workspace_root: Path, max_chars: int = 16000) -> str:
    """Every step of every round: tool, arguments, ok, and the reason or a
    short result summary. The full records stay readable via read_steps."""
    lines = []
    for k, rd in enumerate(_round_dirs(workspace_root), start=1):
        lines.append(f"## round {k}")
        for e in _round_trace(rd):
            call = e.get("tool_call") or {}
            res = e.get("result") if isinstance(e.get("result"), dict) else {}
            brief = res.get("reason") or res.get("note") or ""
            lines.append(
                f"- step {e.get('step')}: {call.get('tool')}"
                f"({json.dumps(call.get('args') or {}, ensure_ascii=False)[:160]})"
                f" -> ok={res.get('ok')} {str(brief)[:160]}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[-max_chars:]


def last_frames(workspace_root: Path, n: int = 2) -> list[Path]:
    """The last camera frames of the last round with frames."""
    for rd in reversed(_round_dirs(workspace_root)):
        views = sorted(rd.glob("final_view_*.jpg"))
        if views:
            return views
        frames = _round_frames(rd)
        if frames:
            return frames[-n:]
    return []


TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "read_steps",
        "description": ("Read sanitized tool-call records (call, arguments, "
                        "result, reasoning) of one round. At most "
                        f"{_MAX_STEPS_PER_CALL} steps per call."),
        "parameters": {"type": "object", "properties": {
            "round": {"type": "integer", "minimum": 1},
            "start": {"type": "integer", "minimum": 0},
            "end": {"type": "integer", "description": "exclusive"},
        }, "required": ["round", "start", "end"]}}},
    {"type": "function", "function": {
        "name": "view_frame",
        "description": ("View a saved head-camera frame of one round. index "
                        "counts from 0; negative counts from the end (-1 = "
                        "last frame of the round)."),
        "parameters": {"type": "object", "properties": {
            "round": {"type": "integer", "minimum": 1},
            "index": {"type": "integer"},
        }, "required": ["round", "index"]}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "Read plan.md or summary.md of this episode.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": ["plan.md", "summary.md"]},
        }, "required": ["name"]}}},
]


def _dispatch(workspace_root: Path, name: str, args: dict[str, Any]):
    """Return (text_result, image_path_or_None)."""
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be a JSON object"}, None
    rounds = _round_dirs(workspace_root)
    if name == "read_document":
        doc = str(args.get("name"))
        if doc not in ("plan.md", "summary.md"):
            return {"ok": False, "error": "unknown document"}, None
        path = workspace_root / doc
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"ok": True, "text": text[:8000]}, None
    try:
        round_index = int(args.get("round", 0))
        if not 1 <= round_index <= len(rounds):
            raise ValueError("round out of range")
        rd = rounds[round_index - 1]
    except (ValueError, TypeError, OverflowError):
        return {"ok": False, "error": f"round must be 1..{len(rounds)}"}, None
    if name == "read_steps":
        trace = _round_trace(rd)
        try:
            start = max(0, int(args.get("start", 0)))
            end = max(start, min(len(trace), int(args.get("end", start + 1)),
                                 start + _MAX_STEPS_PER_CALL))
        except (ValueError, TypeError, OverflowError):
            return {"ok": False, "error": "start and end must be integer indices"}, None
        steps = trace[start:end]
        if len(json.dumps(steps, default=str)) > 12000:
            steps = [{"step": e.get("step"), "tool_call": e.get("tool_call"),
                      "result": str(e.get("result"))[:600]} for e in steps]
        return {"ok": True, "n_steps": len(trace), "steps": steps}, None
    if name == "view_frame":
        frames = _round_frames(rd)
        if not frames:
            return {"ok": False, "error": "no frames saved for this round"}, None
        try:
            frame = frames[int(args.get("index", -1))]
        except (IndexError, ValueError, TypeError, OverflowError):
            return {"ok": False, "error": f"index must be in -{len(frames)}..{len(frames) - 1}"}, None
        return {"ok": True, "frame": frame.name, "n_frames": len(frames)}, frame
    return {"ok": False, "error": f"unknown tool {name}"}, None


def run_review_agent(model: str, system_prompt: str, user_block: str,
                     workspace_root: Path) -> str:
    """Let the Reviewer inspect evidence with tools, then return its final
    JSON verdict text."""
    from roborsi.embodied.agent_loop.messages import _assistant_tool_calls_msg, image_block
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools

    first: list[dict[str, Any]] = []
    for path in last_frames(workspace_root):
        first.append(image_block(path))
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": user_block + (
            "\n\nUse read_steps, view_frame and read_document to inspect "
            "whatever evidence you need (for example the last frame of the "
            "last round to see the current scene). When you have enough, "
            "reply with the verdict JSON only. The attached images are the "
            "last camera frames of the episode so far.")}] + first},
    ]
    for _ in range(_MAX_TOOL_TURNS):
        msg = _call_vlm_tools(model, convo, TOOL_SPECS, request_kind="reviewer")
        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        if not tool_calls:
            return _text(msg)
        convo.append(_assistant_tool_calls_msg(msg, tool_calls))
        images: list[Path] = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result, image = _dispatch(workspace_root, tc.function.name, args)
            if image is not None:
                images.append(image)
            convo.append({"role": "tool", "tool_call_id": tc.id,
                          "name": tc.function.name,
                          "content": json.dumps(result, ensure_ascii=False,
                                                default=str)})
        if images:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": "Requested frame(s): "
                 + ", ".join(p.name for p in images)}]
            for path in images:
                content.append(image_block(path))
            convo.append({"role": "user", "content": content})
    convo.append({"role": "user", "content":
                  "Evidence budget used. Reply now with the verdict JSON only."})
    return _text(_call_vlm_tools(model, convo, TOOL_SPECS,
                                 tool_choice="none", request_kind="reviewer"))


def _text(msg: Any) -> str:
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):
        from roborsi.channels.core.agent import _extract_text_block
        content = "".join(_extract_text_block(c) for c in content)
    return content


# ── Repair evidence for the post-episode review ──
def repair_evidence(trace: list[dict], ns: str, *, repo: Path | None = None) -> str:
    """Accept ONLY the caller's sanitized trace; never inspect simulator state."""
    if ns not in {"libero", "robotwin"}:
        return ""
    root = repo or Path(__file__).resolve().parents[3]
    failures: list[int] = []
    suspect: Counter = Counter()
    for i, event in enumerate(trace):
        call = event.get("tool_call") or {}
        result = event.get("result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            continue
        name = call.get("tool", "")
        # A negative state query is not an execution failure.
        failed = any(result.get(k) is False for k in
                     ("ok", "grasped", "reached", "released", "pushed"))
        if failed:
            failures.append(i)
            if isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_]+", name):
                suspect[name] += 1
    # Keep both the final evidence and the recent failure sequence. The ordinary
    # Reviewer input already supplies the beginning; do not truncate its ending.
    selected = sorted(set(failures[-24:] + list(range(max(0, len(trace)-16), len(trace)))))
    rows = [{"trace_index": i, "event": trace[i]} for i in selected]
    blocks = ["=== ADDITIONAL PUBLIC FAILURE AND FINAL EXECUTION EVIDENCE ===",
              json.dumps(rows, ensure_ascii=False, default=str),
              "=== NATIVE REPAIR SOURCE (not a simulator verdict) ===",
              "Use this source only if the public evidence supports a concrete repair. "
              "Tool failure frequency is a diagnostic lead, not proof of a code bug. "
              "Reviewer authors proposals; actual Manager reviews, validates and publishes. "
              "Do not change adjudication or claim a gain without simulation evidence."]
    added = 0
    for name, _count in suspect.most_common():
        policy = root / "roborsi/embodied/skills/base" / name / ns / "policy.py"
        if not policy.is_file():
            continue
        source = policy.read_text(encoding="utf-8")
        # Never hand an incomplete replacement file to the model as full source.
        if len(source) > 100_000:
            blocks.append(f"{name}: source exceeds context budget; full source omitted.")
            continue
        blocks.extend([str(policy.relative_to(root)),
                       "sha256=" + hashlib.sha256(source.encode()).hexdigest(),
                       "```python\n" + source + "\n```"])
        metadata = policy.with_name("SKILL.md")
        if metadata.is_file():
            blocks.append(metadata.read_text(encoding="utf-8"))
        added += 1
        if added == 2:
            break
    return "\n\n".join(blocks)
