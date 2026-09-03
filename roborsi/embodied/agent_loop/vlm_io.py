"""LLM call / retry / provider helpers for the VLM tool loop.

All litellm / OpenAI / Anthropic dispatch, retry, provider auth, and the
small JSON/text/image parsing helpers the perception calls need. No sim
dependencies — reusable by a real-robot agent loop.
"""

from __future__ import annotations

import base64
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class UsageMetrics:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    vlm_calls: int = 0
    metered_calls: int = 0
    unmetered_calls: int = 0
    vlm_wallclock_s: float = 0.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "vlm_calls": self.vlm_calls,
            "metered_calls": self.metered_calls,
            "unmetered_calls": self.unmetered_calls,
            "vlm_wallclock_s": round(self.vlm_wallclock_s, 3),
        }


_ACTIVE_USAGE: ContextVar[UsageMetrics | None] = ContextVar(
    "roborsi_vlm_usage",
    default=None,
)


@contextmanager
def capture_usage() -> Iterator[UsageMetrics]:
    metrics = UsageMetrics()
    token = _ACTIVE_USAGE.set(metrics)
    try:
        yield metrics
    finally:
        _ACTIVE_USAGE.reset(token)


def merge_usage(*items: UsageMetrics) -> dict[str, int | float]:
    total = UsageMetrics()
    for item in items:
        total.prompt_tokens += item.prompt_tokens
        total.completion_tokens += item.completion_tokens
        total.total_tokens += item.total_tokens
        total.vlm_calls += item.vlm_calls
        total.metered_calls += item.metered_calls
        total.unmetered_calls += item.unmetered_calls
        total.vlm_wallclock_s += item.vlm_wallclock_s
    return total.to_dict()


@contextmanager
def _track_vlm_call() -> Iterator[None]:
    metrics = _ACTIVE_USAGE.get()
    before_metered = metrics.metered_calls if metrics is not None else 0
    started = time.monotonic()
    try:
        yield
    finally:
        if metrics is not None:
            metrics.vlm_calls += 1
            metrics.vlm_wallclock_s += time.monotonic() - started
            if metrics.metered_calls == before_metered:
                metrics.unmetered_calls += 1


def _log_tokens(resp) -> None:
    """Emit a [tokens] line so the live dashboard's token panel can tally usage.
    Best-effort — the usage field shape varies by provider / litellm."""
    u = getattr(resp, "usage", None)
    if u is None:
        return

    def _g(*names: str) -> int:
        for n in names:
            v = getattr(u, n, None)
            if v is None and isinstance(u, dict):
                v = u.get(n)
            if v is not None:
                return int(v)
        return 0
    p = _g("prompt_tokens", "input_tokens")
    c = _g("completion_tokens", "output_tokens")
    t = _g("total_tokens") or (p + c)
    metrics = _ACTIVE_USAGE.get()
    if metrics is not None:
        metrics.prompt_tokens += p
        metrics.completion_tokens += c
        metrics.total_tokens += t
        metrics.metered_calls += 1
    print(f"[tokens] {json.dumps({'prompt': p, 'completion': c, 'total': t})}",
          flush=True)


def _call_vlm(model: str, messages: list[dict[str, Any]]) -> str:
    import litellm

    def request():
        with _track_vlm_call():
            response = litellm.completion(
                model=model,
                messages=messages,
                max_tokens=400,
                temperature=0.0,
                extra_body={"output_config": {"effort": "medium"}},
            )
            _log_tokens(response)
            return response

    response = _retry_litellm(request)
    return response.choices[0].message.content or ""


def _call_vlm_tools(model: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]],
                   thinking_budget: int = 0,
                   tool_choice: str | None = None,
                   prefill: str | None = None) -> Any:
    """Native tool_use call. Routes to OpenAI for openai/* and azure/* model
    prefixes, else Anthropic. Returns object with .content and .tool_calls
    in litellm shape.

    `thinking_budget` only applies to Anthropic models.
    `tool_choice`: "none" to forbid tool use (Anthropic only) — forces text.
    `prefill`: assistant-message prefill text (Anthropic only) — model
        must continue from this text. Strongest way to break silence:
        pre-fill with the response format prefix and the model fills
        in the rest."""
    if model.startswith(("openai/", "azure/", "azure-openai/", "gpt-", "o3", "o4")):
        return _retry_litellm(
            lambda: _openai_call_with_tools(model, messages, tools)
        )
    return _retry_litellm(lambda: _anthropic_call_with_tools(
        model, messages, tools, thinking_budget=thinking_budget,
        tool_choice_override=tool_choice, prefill=prefill))


def _call_vlm_no_tools(model: str, messages: list[dict[str, Any]]) -> str:
    """Plain text completion (no tools). Used by CaP-X-style code-as-policy
    loops where VLM emits a Python program in fenced ```python``` block.
    Returns the text content of the assistant's reply."""
    msg = _call_vlm_tools(model, messages, tools=[])
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        # Anthropic via litellm sometimes wraps as content blocks.
        parts = []
        for b in content:
            text = getattr(b, "text", None)
            if text is None and isinstance(b, dict):
                text = b.get("text", "")
            if text:
                parts.append(text)
        return "".join(parts)
    return str(content or "")


def _openai_call_with_tools(model: str, messages: list[dict[str, Any]],
                             tools: list[dict[str, Any]]) -> Any:
    """OpenAI / Azure OpenAI dispatch. Messages and tools are already in
    OpenAI native shape (litellm-style); response.choices[0].message has the
    .content and .tool_calls attrs the caller expects, so no conversion needed.

    Auth resolution order:
      1. ROBORSI_OPENAI_API_KEY (static, sent as both Bearer + api-key)
      2. OPENAI_API_KEY env var (Azure deployments often use api-key header)
      3. az CLI bearer token (Azure managed identity)
    """
    model_id = model.split("/", 1)[1] if "/" in model and not model.startswith("gpt-") else model
    client = _openai_client()
    if _openai_transport() == "responses":
        return _openai_responses_call(
            client,
            model_id=model_id,
            messages=messages,
            tools=tools,
        )
    sanitized_tools = [_sanitize_openai_tool(t) for t in (tools or [])] or None
    with _track_vlm_call():
        resp = client.chat.completions.create(
            model=model_id, messages=messages, tools=sanitized_tools,
            tool_choice="auto" if sanitized_tools else None,
            max_completion_tokens=2048,
        )
        _log_tokens(resp)
    return resp.choices[0].message


def _openai_transport() -> str:
    import os

    transport = os.environ.get(
        "ROBORSI_OPENAI_TRANSPORT",
        "chat_completions",
    ).strip().lower().replace("-", "_")
    if transport not in {"chat_completions", "responses"}:
        raise ValueError(
            "ROBORSI_OPENAI_TRANSPORT must be 'chat_completions' or 'responses'"
        )
    return transport


def _openai_client() -> Any:
    import json as json_module
    import os

    from openai import OpenAI

    base_url = (
        os.environ.get("ROBORSI_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
    )
    static_key = (
        os.environ.get("ROBORSI_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not static_key:
        try:
            auth_path = Path.home() / ".codex" / "auth.json"
            static_key = json_module.loads(
                auth_path.read_text(encoding="utf-8")
            ).get("OPENAI_API_KEY")
        except (FileNotFoundError, json_module.JSONDecodeError):
            pass
    if not base_url:
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.is_file():
            for line in config_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("base_url"):
                    base_url = stripped.split("=", 1)[1].strip().strip('"')
                    break
    base_url = base_url or "https://api.openai.com/v1"
    headers: dict[str, str] = {}
    if static_key:
        api_key = static_key
        headers["api-key"] = static_key
    else:
        api_key = _azure_bearer_token()
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=headers or None,
        timeout=180.0,
    )


def _openai_responses_call(
    client: Any,
    *,
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    import os

    instructions, input_items = _messages_to_responses_input(messages)
    kwargs: dict[str, Any] = {
        "model": model_id,
        "input": input_items,
        "max_output_tokens": int(
            os.environ.get("ROBORSI_OPENAI_MAX_OUTPUT_TOKENS", "8192")
        ),
    }
    if instructions:
        kwargs["instructions"] = instructions
    response_tools = [_tool_to_responses(tool) for tool in tools]
    if response_tools:
        kwargs["tools"] = response_tools
        kwargs["tool_choice"] = "auto"
    with _track_vlm_call():
        response = client.responses.create(**kwargs)
        _log_tokens(response)
    return _wrap_openai_responses(response)


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            text = _message_text(content)
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": _message_text(content) or "(no output)",
            })
            continue

        tool_calls = message.get("tool_calls") or []
        text = _message_text(content)
        if text:
            if isinstance(content, list):
                items.append({
                    "role": role,
                    "content": _content_to_responses_blocks(content, role=role),
                })
            else:
                items.append({"role": role, "content": text})
        for tool_call in tool_calls:
            function = tool_call.get("function") or {}
            items.append({
                "type": "function_call",
                "call_id": str(tool_call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "arguments": str(function.get("arguments") or "{}"),
            })
    return "\n\n".join(instructions), items


def _content_to_responses_blocks(
    content: list[dict[str, Any]],
    *,
    role: str,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text_type = "output_text" if role == "assistant" else "input_text"
    for block in content:
        block_type = block.get("type")
        if block_type in {"text", "input_text", "output_text"}:
            text = str(block.get("text") or "")
            if text:
                blocks.append({"type": text_type, "text": text})
        elif block_type in {"image_url", "input_image"}:
            image = block.get("image_url")
            image_url = image.get("url") if isinstance(image, dict) else image
            if image_url:
                blocks.append({"type": "input_image", "image_url": image_url})
    return blocks


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            text = str(block.get("text") or "")
            if text:
                parts.append(text)
    return "".join(parts)


def _tool_to_responses(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function") or {}
    converted = {
        "type": "function",
        "name": function["name"],
        "description": function.get("description", ""),
        "parameters": function.get("parameters") or {
            "type": "object",
            "properties": {},
        },
    }
    if "strict" in function:
        converted["strict"] = bool(function["strict"])
    return converted


def _wrap_openai_responses(response: Any) -> Any:
    class _ToolCall:
        def __init__(self, item: Any) -> None:
            self.id = (
                getattr(item, "call_id", None)
                or getattr(item, "id", None)
                or ""
            )
            self.function = type("F", (), {
                "name": getattr(item, "name", ""),
                "arguments": getattr(item, "arguments", "{}") or "{}",
            })()

    class _Message:
        def __init__(self, content: str | None, tool_calls: list[Any]) -> None:
            self.content = content
            self.tool_calls = tool_calls or None

    text_parts: list[str] = []
    tool_calls: list[Any] = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            tool_calls.append(_ToolCall(item))
            continue
        if item_type != "message":
            continue
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) in {"output_text", "text", "refusal"}:
                text = getattr(block, "text", None) or getattr(block, "refusal", None)
                if text:
                    text_parts.append(str(text))
    text = "".join(text_parts)
    return _Message(text or None, tool_calls)


def _sanitize_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """OpenAI rejects array schemas without `items`. Walk the params tree and
    inject `items: {type: number}` for bare arrays (matches the geometry-vector
    use cases in this codebase: quat, rgb, xyz)."""
    import copy
    out = copy.deepcopy(tool)
    params = (out.get("function") or {}).get("parameters") or {}
    _patch_array_items(params)
    return out


def _patch_array_items(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "array" and "items" not in node:
            node["items"] = {"type": "number"}
        for v in node.values():
            _patch_array_items(v)
    elif isinstance(node, list):
        for item in node:
            _patch_array_items(item)


_AZ_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _azure_bearer_token() -> str:
    """Get Azure bearer token via `az` CLI, cached for 50 min."""
    import os
    import subprocess
    import time
    now = time.time()
    if _AZ_TOKEN_CACHE["token"] and now < _AZ_TOKEN_CACHE["expires_at"]:
        return _AZ_TOKEN_CACHE["token"]
    resource = os.environ.get(
        "ROBORSI_AZURE_RESOURCE", "https://cognitiveservices.azure.com/")
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", resource,
         "--query", "accessToken", "-o", "tsv"], text=True).strip()
    _AZ_TOKEN_CACHE["token"] = out
    _AZ_TOKEN_CACHE["expires_at"] = now + 50 * 60
    return out


def _anthropic_client():
    import os

    import anthropic

    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )
    if not api_key:
        raise RuntimeError(
            "Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN before using "
            "an Anthropic-backed role."
        )
    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


def _anthropic_call_with_tools(model: str, messages: list[dict[str, Any]],
                                tools: list[dict[str, Any]],
                                thinking_budget: int = 0,
                                tool_choice_override: str | None = None,
                                prefill: str | None = None) -> Any:
    from roborsi.embodied.agent_loop.messages import _convert_messages_to_anthropic
    client = _anthropic_client()
    model_id = model.split("/", 1)[1] if "/" in model else model
    system_text = ""
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            system_text = content if isinstance(content, str) else json.dumps(content)
            continue
        cleaned.append(msg)
    anthropic_tools = [{
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    } for t in tools]
    anth_msgs = _convert_messages_to_anthropic(cleaned)
    # Prefill the assistant turn: append assistant message starting with
    # the prefill text. Anthropic must continue from there → guarantees
    # non-empty text response. The prefill text is part of the output.
    if prefill:
        anth_msgs = anth_msgs + [{
            "role": "assistant",
            "content": [{"type": "text", "text": prefill}],
        }]
    tc = {"type": "none"} if tool_choice_override == "none" else {"type": "auto"}
    kwargs = dict(
        model=model_id,
        system=system_text,
        messages=anth_msgs,
        tools=anthropic_tools if tool_choice_override != "none" else [],
        tool_choice=tc if anthropic_tools and tool_choice_override != "none" else None,
        max_tokens=64000 + thinking_budget,
        temperature=1.0,
        extra_body={"output_config": {"effort": "medium"}},
    )
    if not kwargs["tools"]:
        kwargs.pop("tools")
        kwargs.pop("tool_choice", None)
    if thinking_budget > 0:
        # Proxy only supports adaptive (not enabled) + effort=medium for opus-4.7.
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["temperature"] = 1.0   # thinking requires temp=1
    # Streaming required for high max_tokens — collect final message from
    # the stream events.
    with _track_vlm_call():
        with client.messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
        return _wrap_anthropic_response(resp)


def _wrap_anthropic_response(resp: Any) -> Any:
    """Mimic litellm message shape with .content and .tool_calls."""
    class _ToolCall:
        def __init__(self, blk):
            self.id = blk.id
            self.function = type("F", (), {
                "name": blk.name,
                "arguments": json.dumps(blk.input, ensure_ascii=False),
            })()

    class _Msg:
        def __init__(self, content_text, tool_calls):
            self.content = content_text
            self.tool_calls = tool_calls

    text = ""
    tool_calls = []
    blocks_count = len(resp.content or [])
    for blk in resp.content or []:
        if blk.type == "text":
            text += blk.text
        elif blk.type == "tool_use":
            tool_calls.append(_ToolCall(blk))
    # DIAGNOSTIC: when both empty, dump full raw response so we can see WHY
    # the LLM went silent. Common causes: stop_reason='max_tokens' (need
    # higher cap), 'refusal' (model refused), 'pause_turn' (need to continue),
    # empty content list with usage info.
    if not text and not tool_calls:
        import sys as _sys
        stop = getattr(resp, "stop_reason", "?")
        usage = getattr(resp, "usage", None)
        print(f"[silence-debug] empty response: stop_reason={stop!r} "
              f"blocks={blocks_count} usage={usage}", file=_sys.stderr,
              flush=True)
        # Dump full content list
        print(f"[silence-debug] content list: {resp.content!r}",
              file=_sys.stderr, flush=True)
    _log_tokens(resp)
    return _Msg(text or None, tool_calls or None)


def _retry_litellm(fn, attempts: int = 4, base_delay: float = 1.5):
    """Retry on transient gateway errors (502, 503, timeout)."""
    import time
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            msg = str(exc).lower()
            transient = any(s in msg for s in (
                "badgateway", "502", "503", "504", "timeout", "rate limit", "overloaded",
            ))
            if not transient or i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** i))
            last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError("retry exhausted with no exception")


def _parse_tool_call(text: str) -> dict[str, Any]:
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    candidate = fence.group(1) if fence else text
    depth = 0
    start = -1
    for i, ch in enumerate(candidate):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(candidate[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return {"tool": "_invalid", "args": {}, "raw": text[:200]}


def _image_dims(path: Path) -> tuple[int, int]:
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        return (0, 0)
    h, w = img.shape[:2]
    return (int(w), int(h))


def _vlm_complete_openai(model: str, system: str, convo: list[dict[str, Any]]) -> str:
    """OpenAI / Azure-OpenAI (codex CLI-style) Chat Completions path.

    Loads OPENAI_API_KEY + base_url from ~/.codex/auth.json + config.toml
    when env vars aren't set. Default model gpt-5 (Azure deployment name; override
    via ROBORSI_VLM_MODEL=openai/<deployment_id>).
    """
    client = _openai_client()
    model_id = model.split("/", 1)[1] if "/" in model else model
    if not model_id or model_id.startswith("anthropic"):
        model_id = "gpt-5"
    if _openai_transport() == "responses":
        message = _openai_responses_call(
            client,
            model_id=model_id,
            messages=[
                {"role": "system", "content": system},
                *convo,
            ],
            tools=[],
        )
        return str(message.content or "")
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in convo:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        parts: list[dict[str, Any]] = []
        for blk in (content or []):
            if blk.get("type") == "text":
                parts.append({"type": "text", "text": blk.get("text", "")})
            elif blk.get("type") == "image_url":
                parts.append({"type": "image_url",
                              "image_url": blk.get("image_url", {})})
        msgs.append({"role": role, "content": parts})
    with _track_vlm_call():
        resp = client.chat.completions.create(
            model=model_id, messages=msgs, max_completion_tokens=16384,
            reasoning_effort="low",
        )
        _log_tokens(resp)
    return resp.choices[0].message.content or ""


def _call_vlm_image(model: str, system: str, user_text: str, image_path: Path) -> str:
    return _call_vlm_image_impl(model, system, user_text, image_path)


def _call_vlm_image_impl(
    model: str,
    system: str,
    user_text: str,
    image_path: Path,
) -> str:
    """Single-image perception query via anthropic SDK direct (proxy needs
    extra_body which litellm strips). Returns the model's text content.

    Routes to OpenAI/Azure when ROBORSI_VLM_PROVIDER=openai OR the
    perception model starts with openai/ / gpt- / o3 / o4. Else Claude."""
    import os
    # Default must be a model the configured account actually serves: the old
    # "openai/gpt-5.4-6" default 404s ("not available on any configured Copilot
    # account"), so any caller that does NOT set ROBORSI_PERCEPTION_MODEL
    # (the harness gate, standalone skill tests — everything except cli_3role,
    # which setdefault's it) crashed on the first VLM perception call. Match
    # cli_3role's working value so the gate can validate perception-dependent
    # grasp skills instead of ERRORing.
    perception_model = os.environ.get(
        "ROBORSI_PERCEPTION_MODEL", "anthropic/claude-sonnet-4-6")
    is_openai_model = perception_model.startswith(
        ("openai/", "azure/", "azure-openai/", "gpt-", "o3", "o4"))
    if (os.environ.get("ROBORSI_VLM_PROVIDER", "anthropic").lower() == "openai"
            or is_openai_model):
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
        convo = [{"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]}]
        return _vlm_complete_openai(perception_model, system, convo)
    raw = image_path.read_bytes()
    suffix = image_path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    b64 = base64.b64encode(raw).decode()
    client = _anthropic_client()
    model_id = perception_model.split("/", 1)[1] if "/" in perception_model else perception_model
    def request():
        with _track_vlm_call():
            response = client.messages.create(
                model=model_id,
                system=system,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": f"image/{suffix}",
                        "data": b64,
                    }},
                ]}],
                max_tokens=512,
                temperature=1.0,
                extra_body={"output_config": {"effort": "medium"}},
            )
            _log_tokens(response)
            return response

    resp = _retry_litellm(request)
    text = ""
    for blk in resp.content or []:
        if getattr(blk, "type", None) == "text":
            text += blk.text
    return text


def _parse_json(text: str) -> dict[str, Any] | None:
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
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None
