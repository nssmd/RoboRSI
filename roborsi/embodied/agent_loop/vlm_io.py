"""LLM call / retry / provider helpers for the VLM tool loop.

All litellm / OpenAI / Anthropic dispatch, retry, provider auth, and the
small JSON/text/image parsing helpers the perception calls need. No sim
dependencies — reusable by a real-robot agent loop.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any


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
    print(f"[tokens] {json.dumps({'prompt': p, 'completion': c, 'total': t})}",
          flush=True)


def _call_vlm(model: str, messages: list[dict[str, Any]]) -> str:
    import litellm
    return _retry_litellm(lambda: litellm.completion(
        model=model,
        messages=messages,
        max_tokens=400,
        temperature=0.0,
        extra_body={"output_config": {"effort": "medium"}},
    )).choices[0].message.content or ""


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
        return _retry_litellm(lambda: _openai_call_with_tools(model, messages, tools))
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
    import os
    from openai import OpenAI
    model_id = model.split("/", 1)[1] if "/" in model and not model.startswith("gpt-") else model
    base_url = (os.environ.get("ROBORSI_OPENAI_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or "https://api.openai.com/v1")
    static_key = (os.environ.get("ROBORSI_OPENAI_API_KEY")
                  or os.environ.get("OPENAI_API_KEY"))
    headers: dict[str, str] = {}
    if static_key:
        api_key = static_key
        headers["api-key"] = static_key  # Azure-style auth
    else:
        api_key = _azure_bearer_token()
    client = OpenAI(api_key=api_key, base_url=base_url, default_headers=headers or None)
    sanitized_tools = [_sanitize_openai_tool(t) for t in (tools or [])] or None
    resp = client.chat.completions.create(
        model=model_id, messages=messages, tools=sanitized_tools,
        tool_choice="auto" if sanitized_tools else None,
        max_completion_tokens=2048,
    )
    _log_tokens(resp)
    return resp.choices[0].message


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
    import os, subprocess, time
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
    import anthropic
    import os

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
    import os
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
    import json as _json
    import os
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        try:
            with open(os.path.expanduser("~/.codex/auth.json")) as f:
                api_key = _json.load(f).get("OPENAI_API_KEY")
        except (FileNotFoundError, _json.JSONDecodeError):
            pass
    if not base_url:
        # Parse codex config.toml for [model_providers.codex] base_url.
        cfg_path = os.path.expanduser("~/.codex/config.toml")
        if os.path.isfile(cfg_path):
            with open(cfg_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("base_url"):
                        base_url = line.split("=", 1)[1].strip().strip('"')
                        break
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0) if base_url else OpenAI(api_key=api_key, timeout=180.0)
    model_id = model.split("/", 1)[1] if "/" in model else model
    if not model_id or model_id.startswith("anthropic"):
        model_id = "gpt-5"
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
    resp = client.chat.completions.create(
        model=model_id, messages=msgs, max_completion_tokens=16384,
        reasoning_effort="low",
    )
    return resp.choices[0].message.content or ""


def _call_vlm_image(model: str, system: str, user_text: str, image_path: Path) -> str:
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
    resp = _retry_litellm(lambda: client.messages.create(
        model=model_id,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image", "source": {"type": "base64", "media_type": f"image/{suffix}", "data": b64}},
        ]}],
        max_tokens=512,
        temperature=1.0,
        extra_body={"output_config": {"effort": "medium"}},
    ))
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
