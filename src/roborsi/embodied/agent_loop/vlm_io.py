"""OpenAI Responses API transport for the public LIBERO runtime."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

_USAGE_LOCK = threading.Lock()
_USAGE_METRICS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "vlm_calls": 0,
    "unmetered_vlm_calls": 0,
}


def _responses_reasoning_effort() -> str:
    requested = os.environ.get("ROBORSI_REASONING_EFFORT", "medium").strip().lower()
    return requested if requested in {"low", "medium"} else "medium"


def reset_usage_metrics() -> None:
    with _USAGE_LOCK:
        for key in _USAGE_METRICS:
            _USAGE_METRICS[key] = 0


def usage_metrics_snapshot() -> dict[str, int]:
    with _USAGE_LOCK:
        return dict(_USAGE_METRICS)


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return 0


def _log_tokens(response: Any) -> None:
    usage = getattr(response, "usage", None)
    with _USAGE_LOCK:
        _USAGE_METRICS["vlm_calls"] += 1
    if usage is None:
        with _USAGE_LOCK:
            _USAGE_METRICS["unmetered_vlm_calls"] += 1
        return
    prompt = _usage_value(usage, "input_tokens", "prompt_tokens")
    completion = _usage_value(usage, "output_tokens", "completion_tokens")
    total = _usage_value(usage, "total_tokens") or prompt + completion
    with _USAGE_LOCK:
        _USAGE_METRICS["prompt_tokens"] += prompt
        _USAGE_METRICS["completion_tokens"] += completion
        _USAGE_METRICS["total_tokens"] += total
    print(
        "[tokens] " + json.dumps({"prompt": prompt, "completion": completion, "total": total}),
        flush=True,
    )


def _compact_responses_image_url(url: str) -> str:
    header, separator, payload = url.partition(",")
    if not separator or not header.lower().startswith("data:image/") or ";base64" not in header:
        return url
    try:
        raw = base64.b64decode(payload, validate=True)
    except (TypeError, ValueError):
        return url
    if len(raw) <= 64 * 1024:
        return url
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return url
    if image is None:
        return url
    variants = [image]
    height, width = image.shape[:2]
    if max(height, width) > 768:
        scale = 768.0 / max(height, width)
        variants.append(
            cv2.resize(
                image,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        )
    smallest: bytes | None = None
    for variant in variants:
        for quality in (85, 75, 65, 55):
            ok, encoded = cv2.imencode(".jpg", variant, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                continue
            candidate = encoded.tobytes()
            if smallest is None or len(candidate) < len(smallest):
                smallest = candidate
            if len(candidate) <= 128 * 1024:
                return "data:image/jpeg;base64," + base64.b64encode(candidate).decode("ascii")
    if smallest is None or len(smallest) >= len(raw):
        return url
    return "data:image/jpeg;base64," + base64.b64encode(smallest).decode("ascii")


def _responses_request_parts(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                instructions.append(content)
            continue
        if role == "tool":
            output = (
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": output or "(no output)",
                }
            )
            continue
        blocks: list[dict[str, Any]] = []
        text_type = "output_text" if role == "assistant" else "input_text"
        if isinstance(content, str) and content.strip():
            blocks.append({"type": text_type, "text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    blocks.append({"type": text_type, "text": str(block["text"])})
                elif block.get("type") == "image_url":
                    image_url = str((block.get("image_url") or {}).get("url") or "")
                    if image_url:
                        blocks.append(
                            {
                                "type": "input_image",
                                "image_url": _compact_responses_image_url(image_url),
                            }
                        )
        if blocks:
            items.append({"type": "message", "role": role, "content": blocks})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
    return "\n\n".join(instructions), items


def _patch_array_items(value: Any) -> Any:
    if isinstance(value, dict):
        patched = {key: _patch_array_items(item) for key, item in value.items()}
        if patched.get("type") == "array" and "items" not in patched:
            patched["items"] = {}
        return patched
    if isinstance(value, list):
        return [_patch_array_items(item) for item in value]
    return value


def _sanitize_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    row = dict(tool)
    function = dict(row.get("function") or {})
    function["parameters"] = _patch_array_items(
        function.get("parameters") or {"type": "object", "properties": {}}
    )
    row["function"] = function
    return row


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tool in tools:
        function = _sanitize_openai_tool(tool).get("function") or {}
        rows.append(
            {
                "type": "function",
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object"},
            }
        )
    return rows


def _retry(call: Callable[[], Any]) -> Any:
    for attempt in range(3):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__}: {exc}".lower()
            transient = any(
                marker in text
                for marker in (
                    "timeout",
                    "connection",
                    "internalservererror",
                    "rate limit",
                    "status code: 429",
                    "status code: 500",
                    "status code: 502",
                    "status code: 503",
                    "status code: 504",
                )
            )
            if not transient or attempt == 2:
                raise
            time.sleep(0.5 * (2**attempt))


def _responses_client():
    from openai import OpenAI

    key = os.environ.get("ROBORSI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    base_url = (
        os.environ.get("ROBORSI_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    return OpenAI(api_key=key, base_url=base_url, timeout=120.0)


def _call_vlm_tools(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    thinking_budget: int = 0,
    tool_choice: str | None = None,
    prefill: str | None = None,
) -> Any:
    del thinking_budget, tool_choice, prefill
    if model != "responses/gpt-5.6-sol":
        raise ValueError("public runtime supports only responses/gpt-5.6-sol")
    instructions, input_items = _responses_request_parts(messages)
    kwargs: dict[str, Any] = {
        "model": model.split("/", 1)[1],
        "input": input_items,
        "max_output_tokens": 4096,
        "reasoning": {"effort": _responses_reasoning_effort()},
    }
    if instructions:
        kwargs["instructions"] = instructions
    response_tools = _responses_tools(tools)
    if response_tools:
        kwargs["tools"] = response_tools
    response = _retry(lambda: _responses_client().responses.create(**kwargs))
    _log_tokens(response)
    calls = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", "") != "function_call":
            continue
        calls.append(
            SimpleNamespace(
                id=str(getattr(item, "call_id", "") or getattr(item, "id", "")),
                function=SimpleNamespace(
                    name=str(getattr(item, "name", "")),
                    arguments=str(getattr(item, "arguments", "{}") or "{}"),
                ),
            )
        )
    return SimpleNamespace(
        content=str(getattr(response, "output_text", "") or ""),
        tool_calls=calls,
        response=response,
    )


def _call_vlm_no_tools(model: str, messages: list[dict[str, Any]]) -> str:
    return str(_call_vlm_tools(model, messages, tools=[]).content or "")


def _call_vlm_image(model: str, system: str, user_text: str, image_path: Path) -> str:
    raw = Path(image_path).read_bytes()
    media = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
    image_url = f"data:{media};base64," + base64.b64encode(raw).decode("ascii")
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    return _call_vlm_no_tools(model, messages)


def _parse_json(text: str) -> dict[str, Any]:
    candidate = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", candidate, re.S)
        if match:
            candidate = match.group(0)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
