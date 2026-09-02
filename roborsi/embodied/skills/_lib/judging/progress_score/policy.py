"""judging.progress_score — single-shot VLM phase-gate."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_MODEL = (
    os.environ.get("ROBORSI_JUDGE_MODEL")
    or os.environ.get("ROBORSI_JUDGE_MODEL")
    or os.environ.get("ROBORSI_VLM_MODEL")
    or "anthropic/claude-sonnet-4-6"
)

SYSTEM_PROMPT = """\
You are a robot phase-gate judge. Given a scene image and a description of
what success should look like for the current phase, return STRICT JSON:
{"done": true|false, "score": 0.0-1.0, "reason": "<1 sentence>"}.
No other text.
"""


def _encode(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"image/{suffix}", base64.b64encode(raw).decode()


def _extract_first_json(text: str) -> dict[str, Any] | None:
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    start = -1
    return None


def run(
    phase: str,
    image_path: str,
    expected: str,
    model: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not phase or not image_path or not expected:
        raise ValueError("progress_score requires phase, image_path, expected")
    path = Path(image_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"image not found: {path}")
    import litellm
    mime, b64 = _encode(path)
    resp = litellm.completion(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Phase: {phase}\nExpected: {expected}"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        max_tokens=200,
        temperature=0.0,
    )
    raw = resp.choices[0].message.content or ""
    parsed = _extract_first_json(raw)
    if parsed is None:
        raise RuntimeError(f"judge did not return JSON. raw={raw[:300]}")
    return {
        "phase": phase,
        "done": bool(parsed.get("done", False)),
        "score": float(parsed.get("score", 0.0)),
        "reason": str(parsed.get("reason", "")),
    }
