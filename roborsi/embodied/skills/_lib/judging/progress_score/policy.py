"""judging.progress_score — single-shot VLM phase-gate."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any


DEFAULT_MODEL = (
    os.environ.get("ROBORSI_JUDGE_MODEL")
    or os.environ.get("ROBORSI_VLM_MODEL")
    or "anthropic/claude-sonnet-4-6"
)

SYSTEM_PROMPT = """\
You are a robot phase-gate judge. Given a scene image and a description of
what success should look like for the current phase, return STRICT JSON:
{"done": true|false, "score": 0.0-1.0, "reason": "<1 sentence>"}.
No other text.
"""


def _extract_first_json(text: str) -> dict[str, Any] | None:
    from roborsi.embodied.agent_loop.vlm_io import _parse_json
    return _parse_json(text)


def _normalize_result(parsed: dict[str, Any]) -> tuple[bool, float]:
    """Accept unambiguous coercions without turning invalid evidence into success."""
    from roborsi.embodied.agent_loop.vlm_io import ProviderError

    done = parsed.get("done", False)
    if isinstance(done, str):
        done = {"true": True, "false": False, "1": True, "0": False}.get(done.strip().lower())
    elif type(done) in (int, float) and done in (0, 1):
        done = bool(done)
    if not isinstance(done, bool):
        raise ProviderError("Progress judge returned an ambiguous done value", category="response")
    score = parsed.get("score", 0.0)
    try:
        if isinstance(score, bool):
            raise ValueError("boolean score")
        score = float(score)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderError("Progress judge returned an invalid score", category="response") from exc
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ProviderError("Progress judge score must be finite and in [0, 1]", category="response")
    return done, score


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
    from roborsi.embodied.agent_loop.messages import image_block
    from roborsi.embodied.agent_loop.vlm_io import ProviderError, is_compatible_model
    selected_model = model or os.environ.get("ROBORSI_JUDGE_MODEL") or DEFAULT_MODEL
    messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": f"Phase: {phase}\nExpected: {expected}"},
                image_block(path),
            ]},
        ]
    if is_compatible_model(selected_model):
        from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
        raw = _call_vlm_tools(selected_model, messages, [],
                              request_kind="perception").content or ""
    else:
        import litellm
        resp = litellm.completion(model=selected_model, messages=messages,
                                  max_tokens=200, temperature=0.0)
        raw = resp.choices[0].message.content or ""
    parsed = _extract_first_json(raw)
    if parsed is None:
        raise ProviderError("Progress judge did not return a JSON object", category="response")
    done, score = _normalize_result(parsed)
    return {
        "phase": phase,
        "done": done,
        "score": score,
        "reason": str(parsed.get("reason", "")),
    }
