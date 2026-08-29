"""Bounded Responses provider readiness checks."""

from __future__ import annotations

from dataclasses import dataclass
import os
from time import perf_counter
from typing import Callable

from roborsi.embodied.agent_loop.vlm_io import _responses_reasoning_effort
from roborsi.embodied.sim.libero.run_records import classify_infrastructure_exception


@dataclass(frozen=True)
class ProbeResult:
    ready: bool
    reason: str
    served_model: str | None = None
    latency_s: float | None = None
    model_mapping: str | None = None


def _normalize_model(model: str | None) -> str:
    text = str(model or "").strip().lower()
    return text.split("/", 1)[-1]


def probe_model(
    model: str,
    *,
    request: Callable[[str], dict],
    model_mapping: dict[str, list[str] | tuple[str, ...] | str] | None = None,
    max_latency_s: float | None = None,
    now: Callable[[], float] = perf_counter,
) -> ProbeResult:
    del model_mapping
    started = now()
    try:
        response = request(model)
    except Exception as exc:  # noqa: BLE001
        category = classify_infrastructure_exception(exc)
        return ProbeResult(False, f"{category}:{type(exc).__name__}")
    latency = max(0.0, now() - started)
    content = str(response.get("content") or "").strip()
    served = str(response.get("model") or "")
    if not content:
        return ProbeResult(False, "empty_response", served_model=served, latency_s=latency)
    if _normalize_model(served) != _normalize_model(model):
        return ProbeResult(False, "served_model_mismatch", served_model=served, latency_s=latency)
    if max_latency_s is not None and latency > max_latency_s:
        return ProbeResult(False, "latency_exceeded", served_model=served, latency_s=latency)
    return ProbeResult(True, "ok", served_model=served, latency_s=latency)


def uses_responses_api(model: str | None) -> bool:
    return str(model or "").strip().lower() == "responses/gpt-5.6-sol"


def responses_probe_request(
    model: str,
    *,
    base_url: str | None = None,
    auth_token: str | None = None,
) -> dict:
    if not uses_responses_api(model):
        raise ValueError("public runtime supports only responses/gpt-5.6-sol")
    from openai import OpenAI

    key = auth_token or os.environ.get("ROBORSI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(
        api_key=key,
        base_url=(
            base_url
            or os.environ.get("ROBORSI_OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ),
        timeout=30.0,
    )
    response = client.responses.create(
        model="gpt-5.6-sol",
        input="Reply OK",
        max_output_tokens=32,
        reasoning={"effort": _responses_reasoning_effort()},
    )
    return {
        "content": str(getattr(response, "output_text", "") or ""),
        "model": str(getattr(response, "model", "") or "gpt-5.6-sol"),
    }


def configured_probe_request(
    model: str,
    *,
    base_url: str | None = None,
    auth_token: str | None = None,
) -> dict:
    return responses_probe_request(model, base_url=base_url, auth_token=auth_token)
