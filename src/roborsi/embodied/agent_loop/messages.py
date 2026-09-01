"""Responses-compatible message construction for the LIBERO tool loop."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any


def _initial_messages(
    instruction: str,
    expected: str,
    *,
    task_name: str = "",
    ns: str = "libero",
    include_skill_task_truth: bool = True,
) -> list[dict[str, Any]]:
    from roborsi.embodied.agent_loop.prompt_tools import _system_prompt

    task_definition = ""
    if task_name and include_skill_task_truth:
        from roborsi.embodied.skills import get

        skill = get(task_name)
        prompts = (
            (((skill.frontmatter or {}).get("metadata") or {}).get("vlm_prompts") or {})
            if skill
            else {}
        )
        stable_goal = str(prompts.get("instruction") or "").strip()
        stable_expected = str(prompts.get("expected_on_success") or "").strip()
        if stable_goal or stable_expected:
            completion = (
                f"\nStable visible completion condition: {stable_expected}"
                if stable_expected
                else ""
            )
            task_definition = (
                "\nStable task definition from the registered skill:\n"
                + stable_goal
                + completion
                + "\nThe runtime instruction remains authoritative when supplied "
                "by the evaluator.\n"
            )
    return [
        {
            "role": "system",
            "content": _system_prompt(ns=ns),
        },
        {
            "role": "user",
            "content": (
                f"Task: {instruction}\n"
                f"Visible completion criterion: {expected}\n"
                f"{task_definition}\n"
                "Begin by issuing at least one registered tool call."
            ),
        },
    ]


def _summarize_old_trace(
    convo: list[dict[str, Any]],
    keep_recent: int = 12,
) -> list[dict[str, Any]]:
    if len(convo) <= keep_recent + 2:
        return convo
    head = list(convo[:2])
    split = max(2, len(convo) - keep_recent)
    while split < len(convo) and convo[split].get("role") == "tool":
        split += 1
    summary: list[str] = []
    for message in convo[2:split]:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                summary.append(f"- {function.get('name', '?')}({function.get('arguments', '{}')})")
        elif message.get("role") == "tool":
            summary.append(f"  result: {str(message.get('content') or '')[:180]}")
    compact = {
        "role": "user",
        "content": "Earlier visible trace summary:\n" + "\n".join(summary[-80:]),
    }
    return head + [compact] + list(convo[split:])


def _image_media_type(raw: bytes, path: Path) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _append_image(convo: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    image_url = f"data:{_image_media_type(raw, path)};base64," + base64.b64encode(raw).decode(
        "ascii"
    )
    from roborsi.embodied.agent_loop.vlm_io import _compact_responses_image_url

    block = {
        "type": "image_url",
        "image_url": {"url": _compact_responses_image_url(image_url)},
    }
    last = convo[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content}, block]
    elif isinstance(content, list):
        content.append(block)
    else:
        last["content"] = [block]
    return convo


def _assistant_tool_calls_msg(msg: Any, tool_calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": getattr(msg, "content", None) or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                },
            }
            for call in tool_calls
        ],
    }


def _sanitize_tool_pairing(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphan tool outputs before sending a resumed conversation."""
    known: set[str] = set()
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "assistant":
            known.update(str(call.get("id") or "") for call in message.get("tool_calls") or [])
            out.append(message)
        elif message.get("role") == "tool":
            if str(message.get("tool_call_id") or "") in known:
                out.append(message)
        else:
            out.append(message)
    return out


def _message_json(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, default=str)
