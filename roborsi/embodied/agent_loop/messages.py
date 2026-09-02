"""Message-list construction + provider-format conversion for the tool loop.

Builds the per-episode conversation, compacts long traces, appends images,
and converts litellm/openai-shaped messages into Anthropic native blocks.
No sim dependencies.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any


def _initial_messages(instruction: str, expected: str,
                       *, task_name: str = "",
                       restrict_to_names: set[str] | None = None,
                       ns: str = "robotwin",
                       ) -> list[dict[str, Any]]:
    """Build the per-episode opening messages, including any PROVEN RECIPES
    auto-extracted from past successful runs of this atomic skill
    (CaP-X-style SkillLibrary, ≥2 occurrences threshold). `ns` selects the
    backend's embodiment/rules in the system prompt (default robotwin)."""
    from roborsi.embodied.agent_loop.prompt_tools import _system_prompt
    recipes_section = ""
    if task_name:
        from roborsi.embodied.skills._lib.library.skill_library import (
            get_proven_recipes, format_recipes_for_prompt,
        )
        recipes = get_proven_recipes(task_name)
        if recipes:
            recipes_section = format_recipes_for_prompt(recipes, task_name)
    return [
        {"role": "system", "content": _system_prompt(restrict_to_names=restrict_to_names, ns=ns)},
        {"role": "user", "content": (
            f"Task: {instruction}\n"
            f"Success criterion: {expected}\n\n"
            f"{recipes_section}"
            f"Begin. Issue exactly one tool call, in JSON, no prose."
        )},
    ]


def _summarize_old_trace(convo: list[dict[str, Any]],
                          keep_recent: int = 12) -> list[dict[str, Any]]:
    """Compact old trace messages into a single summary turn to keep
    conversation under ~20k tokens. Preserves system + initial user
    turn + the `keep_recent` most recent turns; everything else gets
    distilled into "previously tried X (result Y)" lines.

    Per 2026-06-15 user request "trace 累计加一下，达到一定 token 就总结".
    """
    if len(convo) <= keep_recent + 2:
        return convo
    head = [m for m in convo[:2]]  # system + first user instruction
    # Pick the recent-window boundary so it does NOT start on an
    # orphan tool message (role=='tool') or a user message whose
    # tool_result blocks would lose their preceding assistant tool_use.
    # Otherwise Anthropic rejects with "unexpected tool_use_id" (V58).
    split = len(convo) - keep_recent
    while split < len(convo) and convo[split].get("role") in ("tool",):
        split += 1  # skip leading orphan tool results
    # Also: if the boundary lands right after an assistant(tool_calls)
    # whose tool results are in `recent`, that's fine. But if it lands
    # ON a user message carrying tool_result blocks, back up to include
    # the assistant that emitted them — simplest: skip forward past any
    # leading user-with-tool_result too.
    def _is_user_with_tool_result(m: dict) -> bool:
        c = m.get("content")
        return (m.get("role") == "user" and isinstance(c, list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in c))
    while split < len(convo) and _is_user_with_tool_result(convo[split]):
        split += 1
    recent = convo[split:]
    middle = convo[2:split]

    # Distill middle into a single summary block.
    import json as _json
    tool_summary: list[str] = []
    for m in middle:
        role = m.get("role")
        if role == "assistant":
            tcs = m.get("tool_calls") or []
            for tc in tcs:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "?")
                args_raw = fn.get("arguments", "{}")
                try:
                    args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args = {}
                arg_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())[:120]
                tool_summary.append(f"  - {name}({arg_str})")
        elif role == "tool":
            content = m.get("content", "")
            if isinstance(content, str):
                # Extract ok and short note
                snippet = content[:150].replace("\n", " ")
                tool_summary.append(f"    → {snippet}")
    summary_text = (
        "[TRACE SUMMARY of older steps — full detail removed to save context]\n"
        + "\n".join(tool_summary[:80])
        + "\n[END TRACE SUMMARY — most recent steps follow]"
    )
    summary_msg = {"role": "user", "content": summary_text}
    return head + [summary_msg] + recent


def _append_image(convo: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    """Append the latest image to the most recent user turn."""
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode()
    last = convo[-1]
    if isinstance(last.get("content"), str):
        last_text = last["content"]
        last["content"] = [
            {"type": "text", "text": last_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
    elif isinstance(last.get("content"), list):
        last["content"].append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return convo


def _assistant_tool_calls_msg(msg: Any, tool_calls: list[Any]) -> dict[str, Any]:
    """Serialize a litellm assistant message containing tool_calls into the
    OpenAI-format dict that the next API call needs."""
    return {
        "role": "assistant",
        "content": getattr(msg, "content", None) or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ],
    }


def _convert_messages_to_anthropic(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert litellm/openai-style messages to anthropic native shape."""
    out: list[dict[str, Any]] = []
    for m in msgs:
        role = m["role"]
        content = m.get("content")
        if role == "tool":
            tool_use_id = m.get("tool_call_id", "")
            text = content if isinstance(content, str) else json.dumps(content)
            # Anthropic tool_result content can be empty per spec but
            # downstream coerces it; sanitize for safety.
            if not str(text).strip():
                text = "(no text)"
            blk = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            # Coalesce consecutive tool_result blocks into one user message
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(blk)
            else:
                out.append({"role": "user", "content": [blk]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for blk in content:
                    if blk.get("type") == "text":
                        txt = blk.get("text", "")
                        if str(txt).strip():
                            blocks.append(blk)
                    elif blk.get("type") in ("tool_use", "image"):
                        blocks.append(blk)
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id"),
                               "name": fn.get("name"), "input": args})
            if not blocks:
                blocks = [{"type": "text", "text": "(no text)"}]
            out.append({"role": "assistant", "content": blocks})
        elif role == "user":
            if isinstance(content, str):
                # Anthropic rejects empty/whitespace-only text; replace
                # with a single space so the API accepts it.
                out.append({"role": "user",
                            "content": content if content.strip() else "(no text)"})
            elif isinstance(content, list):
                # Convert image_url blocks → anthropic image blocks
                anth_blocks = []
                for blk in content:
                    if blk.get("type") == "text":
                        # Sanitize empty/whitespace text — Anthropic
                        # rejects "messages: text content blocks must
                        # contain non-whitespace text". V23 attempt_1
                        # crashed on exactly this. Replace with single
                        # space; downstream model just sees blank.
                        txt = blk.get("text", "")
                        if not txt or not str(txt).strip():
                            anth_blocks.append({"type": "text", "text": "(no text)"})
                        else:
                            anth_blocks.append(blk)
                    elif blk.get("type") == "image_url":
                        url = (blk.get("image_url") or {}).get("url", "")
                        if url.startswith("data:"):
                            header, _, b64 = url.partition(",")
                            mt = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
                            anth_blocks.append({"type": "image", "source": {
                                "type": "base64", "media_type": mt, "data": b64,
                            }})
                    elif blk.get("type") in ("image", "tool_result", "tool_use"):
                        anth_blocks.append(blk)
                out.append({"role": "user", "content": anth_blocks or [{"type": "text", "text": "(no text)"}]})
            else:
                out.append({"role": "user", "content": "(no text)"})
    return _sanitize_tool_pairing(out)


def _sanitize_tool_pairing(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop any tool_result block whose tool_use_id has no matching
    tool_use in the IMMEDIATELY PRECEDING assistant message. Anthropic
    rejects dangling tool_result blocks (BadRequest "unexpected
    tool_use_id"). This happens when trace summarization (V58) or
    mid-atomic message injection slices a tool_use / tool_result pair
    apart. Also drops assistant tool_use blocks whose result never
    arrives (the next message isn't its tool_result) to keep symmetry.
    """
    # 1. Collect, for each message index, the tool_use ids it emits.
    def _emitted_ids(m: dict) -> set[str]:
        c = m.get("content")
        ids: set[str] = set()
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    ids.add(blk.get("id"))
        return ids

    cleaned: list[dict[str, Any]] = []
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            prev_ids = _emitted_ids(cleaned[-1]) if cleaned and cleaned[-1].get("role") == "assistant" else set()
            new_blocks = []
            for blk in m["content"]:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    if blk.get("tool_use_id") not in prev_ids:
                        continue  # dangling — drop
                new_blocks.append(blk)
            if not new_blocks:
                new_blocks = [{"type": "text", "text": "(no text)"}]
            cleaned.append({"role": "user", "content": new_blocks})
        else:
            cleaned.append(m)
    return cleaned
