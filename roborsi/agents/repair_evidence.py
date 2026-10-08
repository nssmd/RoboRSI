"""Public execution evidence and native source for stateless Reviewer calls."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def repair_evidence(trace: list[dict], ns: str, *, repo: Path | None = None) -> str:
    """Accept ONLY the caller's sanitized trace; never inspect simulator state."""
    if ns not in {"libero", "robotwin"}:
        return ""
    root = repo or Path(__file__).resolve().parents[2]
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
