"""AI audit for VLM-proposed skills.

Calls a VLM with the skill code + docstring + (optional) demo images,
returns a structured audit covering safety, correctness, robustness,
and recommendation. Used by the review HTML server's "AI Audit" button.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


_AUDIT_CACHE: dict[str, dict[str, Any]] = {}


def _build_prompt(*, task_name: str, name: str, docstring: str,
                    code: str, test_result: str, has_images: bool) -> str:
    return f"""\
You are a senior robotics engineer reviewing a Python helper that a
VLM agent proposed for a robot manipulation task. The robot has a
sandbox of base skills (perception, control, grasp generation, etc.).
The agent wants this helper added so it can call it from future code.

Your job: produce a STRUCTURED audit. Be CONCRETE and SPECIFIC — cite
exact lines or function calls when raising concerns. Return ONLY a
JSON object (no prose, no markdown fences) with this schema:

{{
  "verdict": "APPROVE" | "REQUEST_CHANGES" | "REJECT",
  "safety": {{"score": 0..10, "concerns": ["string", ...]}},
  "correctness": {{"score": 0..10, "concerns": ["string", ...]}},
  "robustness": {{"score": 0..10, "concerns": ["string", ...]}},
  "code_quality": {{"score": 0..10, "concerns": ["string", ...]}},
  "matches_docstring": true | false,
  "summary": "one-line gist",
  "specific_fixes": ["actionable change 1", ...]
}}

VERDICT RULES:
- APPROVE: scores >=7 across all dims AND no safety concerns AND
  matches_docstring=true.
- REQUEST_CHANGES: minor issues (specific_fixes lists them).
- REJECT: any safety concern (e.g. destructive action without checks,
  infinite loops, manipulation of files/network), OR fundamental
  correctness issue.

CONTEXT FOR THIS REVIEW:
- task: {task_name}
- skill name: {name}
- skill docstring: {docstring or "(none)"}
- demo result preview (from a test invocation): {(test_result or "(none)")[:300]}
- demo before/after images attached: {"yes" if has_images else "no"}

CODE TO AUDIT:
```python
{code}
```

Return JSON now."""


def audit_skill(*, name: str, code: str, docstring: str,
                  task_name: str = "shared",
                  test_result_preview: str | None = None,
                  test_image_paths: dict[str, str] | None = None,
                  model: str | None = None,
                  use_cache: bool = True) -> dict[str, Any]:
    """Run an AI audit on a proposed skill. Returns structured dict."""
    key = f"{name}:{hash(code)}"
    if use_cache and key in _AUDIT_CACHE:
        return _AUDIT_CACHE[key]

    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image, _call_vlm_no_tools
    model = model or os.environ.get("ROBORSI_PERCEPTION_MODEL", DEFAULT_MODEL)
    prompt = _build_prompt(
        task_name=task_name, name=name,
        docstring=docstring, code=code,
        test_result=test_result_preview or "",
        has_images=bool(test_image_paths),
    )
    sys_msg = ("You are a robotics code reviewer. Reply with ONLY a JSON "
               "object as specified. No prose, no fences.")

    # If demo images available, send the first one as context.
    raw = ""
    if test_image_paths:
        # Use after image preferentially, else before.
        img_path = test_image_paths.get("after") or test_image_paths.get("before")
        if img_path and Path(img_path).exists():
            raw = _call_vlm_image(model, sys_msg, prompt, Path(img_path))
    if not raw:
        raw = _call_vlm_no_tools(model,
                                  [{"role": "system", "content": sys_msg},
                                   {"role": "user", "content": prompt}])
    audit = _parse_audit_json(raw)
    audit["audited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    audit["model"] = model
    audit["raw_response_preview"] = raw[:500]
    _AUDIT_CACHE[key] = audit
    return audit


def _parse_audit_json(raw: str) -> dict[str, Any]:
    """Try to extract JSON from VLM response. Returns a fallback dict on failure."""
    raw = raw.strip()
    # Strip code fences if present.
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if fence:
        raw = fence.group(1)
    # Find first/last brace.
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {"verdict": "REQUEST_CHANGES",
                "summary": "Could not parse audit response as JSON.",
                "raw_response_preview": raw[:300]}
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        return {"verdict": "REQUEST_CHANGES",
                "summary": f"JSON parse error: {e}",
                "raw_response_preview": raw[:300]}
    # Normalize.
    for k in ("safety", "correctness", "robustness", "code_quality"):
        if k not in data:
            data[k] = {"score": None, "concerns": []}
        elif isinstance(data[k], dict):
            data[k].setdefault("score", None)
            data[k].setdefault("concerns", [])
    data.setdefault("verdict", "REQUEST_CHANGES")
    data.setdefault("summary", "")
    data.setdefault("specific_fixes", [])
    data.setdefault("matches_docstring", False)
    return data
