"""LLM call / retry / provider helpers for the VLM tool loop.

All litellm / OpenAI / Anthropic / compatible Chat dispatch, retry, auth, and the
small JSON/text/image parsing helpers the perception calls need. No sim
dependencies — reusable by a real-robot agent loop.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from roborsi.embodied.agent_loop.messages import (
    MessageError, normalize_chat_response, prepare_chat_messages, prepare_chat_tools,
)


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


def _openai_reasoning_effort() -> str | None:
    import os

    value = os.environ.get("ROBORSI_REASONING_EFFORT", "").strip().lower()
    return value or None


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


def record_remote_usage(usage: dict[str, Any]) -> None:
    """Add simulator-side model calls to the caller's active role capture."""
    metrics = _ACTIVE_USAGE.get()
    if metrics is not None:
        for field in metrics.to_dict():
            value = usage.get(field, 0)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"invalid remote usage field: {field}")
            setattr(metrics, field, getattr(metrics, field) + value)


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
    if is_compatible_model(model):
        return _call_vlm_no_tools(model, messages)
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
                   prefill: str | None = None,
                   request_kind: str = "tools") -> Any:
    """Native tool_use call. DeepSeek/Kimi use compatible Chat; OpenAI/Azure
    and Anthropic retain their native transports. Returns .content and
    .tool_calls in the original litellm shape.

    `thinking_budget` only applies to Anthropic models.
    `tool_choice`: "none" forbids tools; each transport decides whether to
        include their definitions in the API request.
    `prefill`: assistant-message prefill text (Anthropic only) — model
        must continue from this text. Strongest way to break silence:
        pre-fill with the response format prefix and the model fills
        in the rest."""
    route = _model_route(model)
    match route:
        case "openai":
            effective_tools = [] if tool_choice == "none" else tools
            return _retry_litellm(
                lambda: _openai_call_with_tools(model, messages, effective_tools)
            )
        case "anthropic":
            return _retry_litellm(lambda: _anthropic_call_with_tools(
                model, messages, tools, thinking_budget=thinking_budget,
                tool_choice_override=tool_choice, prefill=prefill))
        case "chat":
            return _chat_call_with_tools(
                model, messages, tools, tool_choice=tool_choice,
                purpose=request_kind, prefill=prefill,
            )
    raise ValueError(f"Unknown model route: {route}")


def _call_vlm_no_tools(model: str, messages: list[dict[str, Any]], *,
                       request_kind: str = "text") -> str:
    """Plain text completion (no tools). Used by CaP-X-style code-as-policy
    loops where VLM emits a Python program in fenced ```python``` block.
    Returns the text content of the assistant's reply."""
    if request_kind == "perception":
        model = resolve_perception_model(model)
    msg = _call_vlm_tools(model, messages, tools=[], request_kind=request_kind)
    if hasattr(msg, "request_message"):
        from roborsi.embodied.agent_loop.messages import TextReply
        return TextReply(msg)
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


class ProviderError(RuntimeError):
    """An API/configuration failure, not evidence of a failed robot skill."""

    def __init__(self, message: str, *, category: str = "protocol"):
        super().__init__(message)
        self.category = category


_MODEL_ROUTES = (
    (("openai/", "azure/", "azure-openai/", "gpt-", "o3", "o4"), "openai"),
    (("anthropic/", "claude-"), "anthropic"),
    (("deepseek/", "moonshot/", "kimi/"), "chat"),
)


def _model_route(model: str | None) -> str:
    """Resolve a model prefix once, preserving the original Claude fallback.

    Routes select the existing request implementations, not a shared HTTP
    payload. DeepSeek/Kimi still resolve their own credentials and model
    capabilities inside the Chat implementation.
    """
    for prefixes, route in _MODEL_ROUTES:
        if model and model.startswith(prefixes):
            return route
    return "anthropic"


@dataclass(frozen=True)
class _ChatModel:
    provider: str
    model_id: str
    vision: bool
    output_field: str
    max_output: int
    max_body_bytes: int
    context_length: int = 1_000_000


_CHAT_MODELS = {
    ("deepseek", "deepseek-flash"): _ChatModel(
        "deepseek", "deepseek-flash", True, "max_tokens", 393216, 48 * 1024**2
    ),
    ("deepseek", "deepseek-v4-pro"): _ChatModel(
        "deepseek", "deepseek-v4-pro", False, "max_tokens", 393216, 48 * 1024**2
    ),
    ("moonshot", "kimi-k3"): _ChatModel(
        "moonshot", "kimi-k3", True, "max_completion_tokens", 1048576, 100_000_000
    ),
}


def is_compatible_model(model: str | None) -> bool:
    """Compatibility helper for callers that need the Chat-specific policy."""
    return _model_route(model) == "chat"


def _chat_model(model: str) -> _ChatModel:
    provider, _, model_id = model.partition("/")
    if provider == "kimi":
        provider = "moonshot"
    try:
        return _CHAT_MODELS[(provider, model_id)]
    except KeyError:
        raise ProviderError(
            f"Unsupported API model {model!r}; supported: deepseek/deepseek-flash, "
            "deepseek/deepseek-v4-pro (text only), moonshot/kimi-k3 (alias kimi/kimi-k3).",
            category="configuration",
        ) from None


def _api_number(name: str, default: int | float, cast=int, *, minimum=1):
    try:
        value = cast(os.environ.get(name, str(default)))
    except ValueError:
        raise ProviderError(f"{name} must be a number", category="configuration") from None
    import math

    if not math.isfinite(value) or value < minimum:
        raise ProviderError(f"{name} must be >= {minimum}", category="configuration")
    return value


@dataclass(frozen=True)
class _ChatConfig:
    profile: _ChatModel
    base_url: str
    api_key: str = field(repr=False)
    purpose: str = "tools"
    reasoning_effort: str = "high"
    thinking: bool = True
    max_output_tokens: int = 16384
    timeout_s: float = 120.0
    deadline_s: float = 300.0
    attempts: int = 3

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.profile.provider,
            "model": self.profile.model_id,
            "base_url": self.base_url,
            "transport": "chat_completions",
            "purpose": self.purpose,
            "reasoning_effort": self.reasoning_effort,
            "thinking": self.thinking,
            "max_output_tokens": self.max_output_tokens,
            "timeout_s": self.timeout_s,
            "deadline_s": self.deadline_s,
            "attempts": self.attempts,
            "stream": False,
        }


def resolve_api_config(model: str, purpose: str = "tools", *, require_key=True) -> _ChatConfig:
    profile = _chat_model(model)
    if purpose not in {"tools", "text", "perception", "reviewer"}:
        raise ProviderError(f"Unknown API purpose: {purpose}", category="configuration")
    prefix = "DEEPSEEK" if profile.provider == "deepseek" else "MOONSHOT"
    key = os.environ.get(f"{prefix}_API_KEY", "")
    if require_key and not key:
        raise ProviderError(f"Set {prefix}_API_KEY for {model}", category="configuration")
    default_url = (
        "https://api.deepseek.com" if prefix == "DEEPSEEK" else "https://api.moonshot.ai/v1"
    )
    base_url = os.environ.get(f"{prefix}_BASE_URL", default_url).rstrip("/")
    try:
        url = urlsplit(base_url)
        _ = url.port
    except ValueError:
        raise ProviderError(f"Invalid {prefix}_BASE_URL", category="configuration") from None
    if (
        url.scheme not in {"https", "http"}
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ProviderError(
            f"{prefix}_BASE_URL must be an HTTP(S) endpoint without credentials/query",
            category="configuration",
        )
    effort = (
        os.environ.get(f"ROBORSI_API_{purpose.upper()}_REASONING_EFFORT")
        or os.environ.get("ROBORSI_API_REASONING_EFFORT")
        or os.environ.get("ROBORSI_REASONING_EFFORT")
        or "high"
    ).lower()
    thinking = True
    if prefix == "DEEPSEEK":
        toggle = os.environ.get("ROBORSI_DEEPSEEK_THINKING", "enabled").lower()
        if toggle not in {"enabled", "disabled"}:
            raise ProviderError(
                "ROBORSI_DEEPSEEK_THINKING must be enabled or disabled", category="configuration"
            )
        thinking = toggle == "enabled" and effort != "none"
        effort = {"minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max"}.get(
            effort, effort
        )
    if effort not in (
        {"low", "high", "max", "none"} if prefix == "DEEPSEEK" else {"low", "high", "max"}
    ):
        raise ProviderError(
            f"Invalid reasoning effort {effort!r} for {model}", category="configuration"
        )
    default_output = 8192 if purpose == "perception" else 16384
    output = _api_number(
        f"ROBORSI_API_{purpose.upper()}_MAX_OUTPUT_TOKENS",
        _api_number("ROBORSI_API_MAX_OUTPUT_TOKENS", default_output),
    )
    if output > profile.max_output or output >= profile.context_length:
        raise ProviderError(
            f"Output budget exceeds {model}'s output/context limit", category="configuration"
        )
    return _ChatConfig(
        profile,
        base_url,
        key,
        purpose,
        effort if thinking else "none",
        thinking,
        output,
        _api_number("ROBORSI_API_TIMEOUT_S", 120, float),
        _api_number("ROBORSI_API_DEADLINE_S", 300, float),
        _api_number("ROBORSI_API_ATTEMPTS", 3),
    )


_PERCEPTION_DEFAULT: ContextVar[str | None] = ContextVar("perception_default", default=None)


@contextmanager
def perception_default(model: str | None):
    token = _PERCEPTION_DEFAULT.set(model)
    try:
        yield
    finally:
        _PERCEPTION_DEFAULT.reset(token)


def with_perception_default(fn):
    """Keep inherited perception local to an execution, including worker contexts."""

    @wraps(fn)
    def wrapped(*args, **kwargs):
        from roborsi.embodied.agent_loop.config import DEFAULT_MODEL

        # Use the execution loop's actual model, including its original default.
        # Do not infer a different model from the environment or persist it there.
        model = (
            kwargs.get("model")
            or fn.__globals__.get("DEFAULT_MODEL")
            or DEFAULT_MODEL
        )
        kwargs["model"] = model
        with perception_default(model):
            return fn(*args, **kwargs)

    return wrapped


def resolve_perception_model(fallback: str | None = None) -> str:
    return (
        os.environ.get("ROBORSI_PERCEPTION_MODEL")
        or _PERCEPTION_DEFAULT.get()
        or fallback
        or "anthropic/claude-sonnet-4-6"
    )


def _chat_request(
    config: _ChatConfig, messages: list[dict], tools: list[dict], tool_choice: str | None = None
) -> dict[str, Any]:
    choice = tool_choice or "auto"
    if choice not in {"auto", "none", "required"}:
        raise ProviderError(
            "Named tool choices are not supported in thinking mode", category="configuration"
        )
    if choice == "required" and (
        not tools or (config.profile.provider == "deepseek" and config.thinking)
    ):
        raise ProviderError(
            "required needs tools and is unsupported in DeepSeek thinking mode",
            category="configuration",
        )
    try:
        prepared = prepare_chat_messages(
            messages, require_reasoning=config.thinking and (
                bool(tools) or config.profile.provider == "moonshot"),
            vision=config.profile.vision,
            image_prefixes=("data:image/", "ms://") if config.profile.provider == "moonshot" else None,
        )
        prepared_tools = prepare_chat_tools(tools) if tools else []
    except MessageError as exc:
        raise ProviderError(str(exc), category=exc.category) from exc
    kwargs: dict[str, Any] = {
        "model": config.profile.model_id,
        "messages": prepared,
        config.profile.output_field: config.max_output_tokens,
        "stream": False,
    }
    if config.profile.provider == "deepseek":
        kwargs["extra_body"] = {"thinking": {"type": "enabled" if config.thinking else "disabled"}}
    if config.thinking:
        kwargs["reasoning_effort"] = config.reasoning_effort
    if tools:
        kwargs.update(tools=prepared_tools, tool_choice=choice)
    body_size = len(json.dumps(kwargs, ensure_ascii=True).encode())
    if body_size > config.profile.max_body_bytes:
        raise ProviderError(
            "Encoded API request exceeds the provider body-size limit", category="capacity"
        )
    _validate_chat_images(kwargs["messages"], config)
    # UTF-8 byte length is a conservative text estimate, not a vendor tokenizer.
    # Images have separate model-dependent token accounting, checked by the API.
    text_bytes = 0
    for message in kwargs["messages"]:
        for key in ("content", "reasoning_content", "tool_calls"):
            value = message.get(key)
            if key == "content" and isinstance(value, list):
                value = [part.get("text", "") for part in value if part.get("type") == "text"]
            if value is not None:
                text_bytes += len((value if isinstance(value, str) else json.dumps(value)).encode())
    text_bytes += len(json.dumps(kwargs.get("tools", []), ensure_ascii=False).encode())
    if text_bytes + config.max_output_tokens > config.profile.context_length:
        raise ProviderError(
            "Conservative text estimate plus output budget exceeds context; no history was silently removed",
            category="capacity",
        )
    return kwargs


def _validate_chat_images(messages: list[dict], config: _ChatConfig) -> None:
    """Check local image inputs; remote references are validated by the API."""
    urls = [
        part["image_url"]["url"]
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    if not urls:
        return
    import base64
    import io

    from PIL import Image, UnidentifiedImageError

    deepseek = config.profile.provider == "deepseek"
    if deepseek and len(urls) > 600:
        raise ProviderError("DeepSeek supports at most 600 input images", category="capacity")
    for url in urls:
        if not url.startswith("data:"):
            continue
        try:
            header, encoded = url.split(",", 1)
            if not header.endswith(";base64"):
                raise ValueError("expected base64")
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as img:
                if header != f"data:{Image.MIME.get(img.format)};base64":
                    raise ValueError("image MIME does not match the bytes")
                max_side = 4096 if len(urls) >= 15 else 8192
                if deepseek and (len(raw) > 32 * 1024**2 or max(img.size) > max_side):
                    raise ProviderError(
                        "DeepSeek input image exceeds its byte/dimension limit", category="capacity"
                    )
        except (ValueError, UnidentifiedImageError, OSError) as exc:
            raise ProviderError("Invalid base64 image input", category="configuration") from exc


def _chat_call_with_tools(
    model: str,
    messages: list[dict],
    tools: list[dict],
    *,
    tool_choice: str | None = None,
    purpose="tools",
    prefill: str | None = None,
) -> Any:
    """One bounded retry layer. Never retry or execute robot tools here."""
    if prefill:
        raise ProviderError("Assistant prefill is not enabled for this API adapter", category="configuration")
    from openai import APIConnectionError, APIResponseValidationError, APIStatusError, OpenAI

    config = resolve_api_config(model, purpose)
    kwargs = _chat_request(config, messages, tools, tool_choice)
    deadline = time.monotonic() + config.deadline_s
    client = OpenAI(
        api_key=config.api_key, base_url=config.base_url, max_retries=0, timeout=config.timeout_s
    )
    try:
        for attempt in range(config.attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("API retry deadline exceeded", category="transport")
            try:
                with _track_vlm_call():
                    response = client.chat.completions.create(
                        **kwargs, timeout=min(config.timeout_s, remaining)
                    )
                    _log_tokens(response)
                if time.monotonic() > deadline:
                    raise ProviderError(
                        "API retry deadline exceeded; no action executed", category="transport"
                    )
                message = normalize_chat_response(
                    response, forbid_tools=not tools or tool_choice == "none"
                )
                if (
                    config.thinking
                    and (tools or config.profile.provider == "moonshot")
                    and "reasoning_content" not in message.request_message
                ):
                    raise ProviderError(
                        "Thinking API response lacks reasoning_content; no action executed"
                    )
                return message
            except (APIConnectionError, APIStatusError) as exc:
                status = getattr(exc, "status_code", None)
                retryable = status in {408, 409, 429, 500, 502, 503, 504} or isinstance(
                    exc, APIConnectionError
                )
                category = (
                    "rate_limit"
                    if status == 429
                    else "transport"
                    if retryable
                    else "configuration"
                    if status in {401, 403, 404}
                    else "protocol"
                )
                if not retryable or attempt + 1 == config.attempts:
                    raise ProviderError(
                        f"{config.profile.provider} API failed ({type(exc).__name__}, status={status}); no fallback provider used",
                        category=category,
                    ) from exc
                delay = 1.5 * (2**attempt)
                headers = getattr(getattr(exc, "response", None), "headers", {})
                try:
                    delay = max(delay, float(headers.get("retry-after", 0)))
                except (TypeError, ValueError):
                    pass
                if delay >= deadline - time.monotonic():
                    raise ProviderError(
                        "API retry deadline exceeded", category="transport"
                    ) from exc
                time.sleep(delay)
            except MessageError as exc:
                raise ProviderError(str(exc), category=exc.category) from exc
            except (APIResponseValidationError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    "API returned an invalid completion response; no action executed"
                ) from exc
        raise ProviderError("API attempts exhausted", category="transport")
    finally:
        client.close()


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
    kwargs: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "tools": sanitized_tools,
        "tool_choice": "auto" if sanitized_tools else None,
        "max_completion_tokens": 2048,
    }
    reasoning_effort = _openai_reasoning_effort()
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    with _track_vlm_call():
        resp = client.chat.completions.create(**kwargs)
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
    reasoning_effort = _openai_reasoning_effort()
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
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
    if is_compatible_model(model):
        return _call_vlm_no_tools(model, [{"role": "system", "content": system}, *convo])
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

    An explicit perception model wins; otherwise inherit the current execution
    model. Route by that model so a stale provider override cannot redirect it.
    """
    perception_model = resolve_perception_model(model)
    route = _model_route(perception_model)
    from roborsi.embodied.agent_loop.messages import image_block
    image = image_block(image_path)
    if route == "chat":
        return _call_vlm_no_tools(perception_model, [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text}, image,
            ]},
        ], request_kind="perception")
    if route == "openai":
        convo = [{"role": "user", "content": [
            {"type": "text", "text": user_text},
            image,
        ]}]
        return _vlm_complete_openai(perception_model, system, convo)
    header, b64 = image["image_url"]["url"].split(",", 1)
    mime = header.removeprefix("data:").split(";", 1)[0]
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
                        "media_type": mime,
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
    # Decode JSON strings/escapes correctly; braces inside a reason or code
    # string are not object boundaries. Keep accepting fenced/prose responses.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            result, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    return None
