"""Extract reusable base skills from successful trace history.

Algorithm:
  1. Pull recent successful runs for the given training tasks from sqlite.
  2. For each run, fetch its inner steps (the per-substep tool calls
     captured by robotwin_agent).
  3. Hand the (task → step sequence) bundle to an LLM with a prompt
     asking it to identify substep patterns that repeat across ≥2 tasks
     and would benefit from being a named ``base/<name>`` skill.
  4. The LLM emits one or more proposal objects, each containing a
     unified diff that creates ``base/<name>/SKILL.md`` and
     ``base/<name>/policy.py``.
  5. Each proposal lands in the sqlite ``proposals`` table (status=
     pending) so ``auto_apply`` can apply + bench-verify it.

Designed to be called once per ``selfevo`` round, between training
rollouts and the test-set bench.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from roborsi.store import trace_db as _td


_SYSTEM_PROMPT = """\
You are a robotics-skill librarian. Your job is to look at successful
trace logs from multiple atomic robot tasks and identify repeated
SUB-ROUTINES that could be extracted into reusable BASE skills under
`roborsi/embodied/skills/base/<name>/`.

A base skill in roborsi has two files:

  base/<name>/SKILL.md   (YAML frontmatter + Overview/Prerequisites/
                          Phases/Success criteria/Failure modes)
  base/<name>/policy.py  (Python with a `def run(env, **kwargs)`
                          function that calls the lower-level tool
                          functions in the requested order)

WHAT COUNTS AS A USEFUL BASE SKILL:
  - 2–5 consecutive substep tool calls that appear in ≥2 different tasks
  - Has a clear semantic name (e.g. "look_and_localize", "grasp_then_lift",
    "verify_contact_then_done")
  - Generic enough that future tasks would re-use it as-is

DO NOT propose:
  - One-tool wrappers (just call the tool directly)
  - Task-specific sequences (only seen in one task)
  - Already-existing base skills (you'll be shown the current `base/` list)

OUTPUT FORMAT (strict JSON, no markdown wrapping):

{
  "proposals": [
    {
      "name": "look_and_localize",
      "rationale": "Appears in tasks click_bell + pick_block_bicoord
                    as look → find_pixel → unproject_pixel chain (3 calls).",
      "diff": "diff --git a/roborsi/embodied/skills/base/look_and_localize/SKILL.md b/...\\n...full unified diff that creates BOTH files..."
    }
  ]
}

If no useful patterns are found, return {"proposals": []}.
"""


def _gather_traces(tasks: list[str], min_runs_per_task: int = 1,
                    max_runs_per_task: int = 5) -> dict[str, list[dict]]:
    """Returns {task: [trace, ...]} where trace = list of inner steps in order."""
    out: dict[str, list[dict]] = {}
    for task in tasks:
        runs = _td.list_runs(skill=f"{task}.zeroshot",
                              status="success", run_mode="evolve",
                              limit=max_runs_per_task)
        traces: list[list[dict]] = []
        for r in runs:
            steps = _td.list_steps(run_id=r["id"], layer="inner")
            seq = [
                {"step": s["idx"], "tool": s["tool"],
                 "args": _decode_args(s.get("args_json")),
                 "ok": bool(s.get("result_ok"))}
                for s in steps if s.get("tool")
            ]
            if seq:
                traces.append(seq)
        if len(traces) >= min_runs_per_task:
            out[task] = traces
    return out


def _decode_args(s: str | None) -> Any:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}


def _existing_base_skills() -> list[str]:
    from roborsi.embodied.skills import discover
    return [sk.name for sk in discover()
             if (sk.frontmatter or {}).get("kind") == "base"]


def extract_candidates(
    train_tasks: list[str],
    model: str | None = None,
    max_proposals: int = 3,
    max_runs_per_task: int = 5,
) -> list[str]:
    """Run one extraction pass. Returns list of new proposal ids.

    Records each candidate to the sqlite ``proposals`` table with
    ``kind='new'`` and the candidate's full diff."""
    from roborsi.runtime_mode import require_evolution
    require_evolution("extracting new base-skill candidates")
    bundle = _gather_traces(train_tasks, max_runs_per_task=max_runs_per_task)
    if not bundle:
        return []
    user_msg = {
        "existing_base_skills": _existing_base_skills(),
        "max_new_proposals": max_proposals,
        "training_tasks": {
            task: [
                {"task": task, "trace": trace}
                for trace in traces
            ]
            for task, traces in bundle.items()
        },
    }
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_no_tools
    reply = _call_vlm_no_tools(
        model or DEFAULT_MODEL,
        [{"role": "system", "content": _SYSTEM_PROMPT},
         {"role": "user", "content": json.dumps(user_msg, ensure_ascii=False,
                                                  default=str)[:32000]}],
    )
    data = _parse_json_block(reply)
    if not data or not isinstance(data.get("proposals"), list):
        return []
    new_ids: list[str] = []
    for prop in data["proposals"][:max_proposals]:
        name = (prop.get("name") or "").strip()
        diff = (prop.get("diff") or "").strip()
        rationale = (prop.get("rationale") or "").strip()
        if not name or not diff:
            continue
        pid = _td.record_proposal(
            skill=f"base.{name}", kind="new",
            diff=diff, rationale=rationale,
            file_path=f"roborsi/embodied/skills/base/{name}/SKILL.md")
        new_ids.append(pid)
    return new_ids


def _parse_json_block(text: str) -> dict[str, Any] | None:
    """LLMs sometimes wrap JSON in ```json fences or add prose. Strip
    fences and try to find the outermost {...} block."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Fall back: take from first { to last }
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            return None
    return None
