"""Message-list construction + provider-format conversion for the tool loop.

Builds the per-episode conversation, compacts long traces, appends images,
and converts litellm/openai-shaped messages into Anthropic native blocks.
No sim dependencies.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


COMPRESSION_POLICY = "tool_trace_only_v3:threshold=30,recent=12,complete_groups_and_observations,all_providers"
OBSERVATION_POLICY = "tool_boundary_images_v2:latest_pending_state,last_per_view,semantic_images,history_unchanged,all_providers"
_OBSERVATION_HEADER = (
    "[TOOL OBSERVATIONS] Images are in capture order. They may show different "
    "views or intermediate states; use the source labels below."
)


class MessageError(ValueError):
    """An invalid chat history or response, before any tool may execute."""

    def __init__(self, message: str, *, category: str = "protocol"):
        super().__init__(message)
        self.category = category

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
    """Compact old trace messages into a single summary turn. Preserves
    system + initial user turn + at least the `keep_recent` most recent
    messages, expanding the window to keep tool-call groups intact.
    Older tool calls and results become "previously tried X (result Y)" lines.

    Per 2026-06-15 user request "trace 累计加一下，达到一定 token 就总结".
    """
    if len(convo) <= keep_recent + 2:
        return convo
    head = [m for m in convo[:2]]  # system + first user instruction
    # Every provider uses assistant.tool_calls + role=tool here; native
    # provider conversion happens later, at the model I/O boundary.
    # Expand backwards if the cut splits a call group. Pin unfinished
    # groups as well, even when that prevents compaction altogether.
    split = len(convo) - keep_recent
    pending: dict[str, int] = {}
    group_start = None
    for index, message in enumerate(convo):
        if message.get("role") == "assistant":
            group_start = index if message.get("tool_calls") else None
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = index
        elif message.get("role") == "tool":
            start = pending.pop(message.get("tool_call_id"), None)
            if start is not None and start < split <= index:
                split = start
        elif (message.get("role") == "user"
              and isinstance(message.get("content"), list)
              and message["content"]
              and message["content"][0].get("text") == _OBSERVATION_HEADER):
            # A tool batch's observations belong to the same retained group.
            if group_start is not None and group_start < split <= index:
                split = group_start
        else:
            group_start = None
    if pending:
        split = min(split, *pending.values())
    if split <= 2:
        return convo
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
                if not isinstance(args, dict):
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


def image_block(path: Path, *, data: bytes | None = None) -> dict[str, Any]:
    """Use actual file encoding, including PNG files with misleading suffixes."""
    import io
    from PIL import Image
    raw = path.read_bytes() if data is None else data
    with Image.open(io.BytesIO(raw)) as img:
        mime = Image.MIME.get(img.format)
        if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            raise ValueError(f"Unsupported observation image format: {img.format}")
    return {"type": "image_url", "image_url": {
        "url": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
    }}


def _append_image(convo: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    """Append one explicit observation in a user message for every provider."""
    convo.append({"role": "user", "content": [
        {"type": "text", "text": _OBSERVATION_HEADER},
        {"type": "text", "text": f"Image: {path.name}"},
        image_block(path),
    ]})
    return convo


def _observation_generation(state: Any) -> tuple[int, int]:
    """Read state identity without acquiring another camera observation."""
    generation = getattr(state.env, "sensor_generation", None)
    if callable(generation):
        return tuple(generation())
    return (0, state._image_physics_steps)


def _tool_image_metadata(state: Any, tool: str, args: Any, result: Any) -> dict[str, Any]:
    """Identify known views; keep unfamiliar/derived tool images distinct."""
    args = args if isinstance(args, dict) else {}
    result = result if isinstance(result, dict) else {}
    camera = result.get("camera")
    kind = "tool"
    generation = _observation_generation(state)
    source = None
    if tool in {"look", "initial", "after_script"}:
        kind = "camera"
        camera = camera or args.get("camera") or "head_camera"
    elif tool == "scan_wrist":
        kind = "camera"
        camera = camera or f"{args.get('arm', 'right')}_camera"
    elif tool == "verify_holding_visual":
        kind, camera = "camera", "head_camera"
    elif tool == "find_object_via_wrist" and result.get("wrist_image") == str(state.last_image_path):
        # On early failure this tool can leave a pre-motion head frame. Only
        # its explicitly returned wrist image is known to be post-motion.
        kind = "camera"
        arm = str(result.get("arm") or args.get("arm", "right")).lower()
        camera = f"{arm}_camera"
    elif tool == "zoom_in":
        kind = "crop"
        previous = state._last_image_observation
        if previous is not None:
            camera = previous["camera"]
            generation = previous["generation"]
            source = previous["sequence"]
    elif tool in {"observe_orbit", "mark_orbit_point"}:
        view = str(args.get("view") or "").strip()
        camera = f"orbit/{view}" if view else None
        kind = ("orbit" if view else "contact_sheet") if tool == "observe_orbit" else "annotation"
    elif tool == "preview_move_to_pose":
        kind = "preview"
    elif tool == "label_points_grid":
        kind, camera = "annotation", "head_camera"
    return {"camera": camera, "kind": kind, "generation": generation,
            "source": source, "views": result.get("views")}


def _capture_tool_image(state: Any, tool: str, args: Any = None,
                        result: Any = None) -> None:
    """Freeze a tool's published image before a later tool can overwrite it.

    Skills keep their existing last_image_path interface. File contents and
    modification time detect refreshes at a fixed path; unchanged cached files
    are not queued again by unrelated tools. Internal perception/debug images
    that were never published through last_image_path are not collected.
    """
    if state.last_image_path is None:
        return
    path = Path(state.last_image_path)
    raw = path.read_bytes()
    fingerprint = (str(path.resolve()), path.stat().st_mtime_ns,
                   hashlib.sha256(raw).hexdigest())
    # A successful explicit camera read is a new observation even if the
    # pixels and filesystem timestamp happen to be identical. Other tools
    # must not republish a cached file merely because the scene advanced.
    explicit_look = (tool in {"look", "scan_wrist"}
                     and isinstance(result, dict) and result.get("ok") is True)
    if fingerprint == state._image_fingerprint and not explicit_look:
        return
    block = image_block(path, data=raw)
    sequence = state._image_sequence + 1
    record = {"sequence": sequence, "tool": tool, "file": path.name,
              "image": block, **_tool_image_metadata(state, tool, args, result)}
    state._pending_images.append(record)
    state._last_image_observation = record
    state._image_sequence = sequence
    state._image_fingerprint = fingerprint


def _append_pending_images(convo: list[dict[str, Any]], state: Any) -> list[dict[str, Any]]:
    """Select within this pending batch only; never rewrite image history."""
    if state._pending_images:
        generation = max(record["generation"] for record in state._pending_images)
        latest = [record for record in state._pending_images
                  if record["generation"] == generation]
        # Only ordinary views replace each other. Crops, markers, previews,
        # contact sheets and unfamiliar skill outputs carry distinct meaning.
        last_view = {record["camera"]: record["sequence"] for record in latest
                     if record["kind"] in {"camera", "orbit"} and record["camera"]}
        current = _observation_generation(state)
        note = f"Selected image stage in this tool batch: {generation}; current state: {current}."
        if generation != current:
            note += " The environment advanced after these images; they are not a fresh observation."
        content = [{"type": "text", "text": _OBSERVATION_HEADER},
                   {"type": "text", "text": note}]
        for record in latest:
            if (record["kind"] in {"camera", "orbit"} and record["camera"]
                    and last_view[record["camera"]] != record["sequence"]):
                continue
            label = (f"Observation {record['sequence']}: tool={record['tool']}, "
                     f"file={record['file']}, kind={record['kind']}, "
                     f"state={record['generation']}")
            if record["camera"]:
                label += f", camera={record['camera']}"
            if record["views"]:
                label += f", views={record['views']}"
            if record["source"] is not None:
                label += f", source_observation={record['source']}"
            if record["kind"] == "tool":
                label += "; state is publication state; capture state is unspecified"
            content.extend([{"type": "text", "text": label}, record["image"]])
        convo.append({"role": "user", "content": content})
        state._pending_images.clear()
    # Keep last_image_path intact: zoom/crop skills still need that file.
    return convo


def _assistant_tool_calls_msg(msg: Any, tool_calls: list[Any]) -> dict[str, Any]:
    """Serialize a litellm assistant message containing tool_calls into the
    OpenAI-format dict that the next API call needs."""
    if hasattr(msg, "request_message"):
        message = serialize_assistant_message(msg)
        selected = {tc["id"]: tc for tc in message.get("tool_calls", [])}
        message["tool_calls"] = [selected[tc.id] for tc in tool_calls]
        return message
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


def serialize_assistant_message(msg: Any, *, empty: str = " ",
                                legacy_content: str | None = None) -> dict[str, Any]:
    """Serialize retained API fields; never persist response-level metadata."""
    if hasattr(msg, "request_message"):
        return copy.deepcopy(msg.request_message)
    # Legacy text-only branches used a single space for empty content.
    content = (legacy_content if legacy_content is not None
               else msg if isinstance(msg, str) else getattr(msg, "content", ""))
    return {"role": "assistant", "content": content or empty}


class TextReply(str):
    """A backwards-compatible text result with its original assistant fields.

    Multi-turn callers should use serialize_assistant_message(reply). This
    keeps Kimi reasoning through code-generation repair rounds as well.
    """

    def __new__(cls, message):
        result = super().__new__(cls, message.content or "")
        result.request_message = serialize_assistant_message(message)
        return result


def prepare_chat_tools(tools: list[dict]) -> list[dict]:
    """Preserve schema semantics; an unconstrained array remains unconstrained."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    result = copy.deepcopy(tools)
    names: set[str] = set()
    for tool in result:
        fn = tool.get("function", {})
        name = fn.get("name")
        if tool.get("type") != "function" or not isinstance(name, str) or not name or name in names:
            raise MessageError(
                "Tool definitions require unique function names", category="configuration"
            )
        names.add(name)
        if fn.get("strict"):
            raise MessageError(
                "Strict tool schemas are not enabled for this adapter", category="configuration"
            )
        schema = fn.get("parameters", {"type": "object", "properties": {}})
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise MessageError(
                f"Invalid schema for {name}: {exc.message}", category="configuration"
            ) from exc
    return result


def skipped_results(calls: list[Any], reason: str) -> list[dict]:
    return [
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(
                {
                    "ok": False,
                    "executed": False,
                    "skipped": True,
                    "reason": reason,
                }
            ),
        }
        for call in calls
    ]


def prepare_chat_messages(messages: list[dict], *, require_reasoning: bool = False,
                          vision: bool = True,
                          image_prefixes: tuple[str, ...] | None = None) -> list[dict]:
    """Validate retained history without changing reasoning or synthesizing it."""
    output: list[dict] = []
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise MessageError(f"Unsupported message role {role!r}")
        if pending and role != "tool":
            raise MessageError("Tool results must be complete before the next message")
        item = {"role": role, "content": copy.deepcopy(message.get("content"))}
        if role == "assistant":
            if "reasoning_content" in message:
                item["reasoning_content"] = message["reasoning_content"]
            elif require_reasoning:
                raise MessageError("Retained assistant message is missing reasoning_content")
            if message.get("tool_calls"):
                item["tool_calls"] = copy.deepcopy(message["tool_calls"])
                for call in item["tool_calls"]:
                    cid = call.get("id")
                    if not isinstance(cid, str) or not cid or cid in pending:
                        raise MessageError("Missing or duplicate tool call ID")
                    pending.add(cid)
        elif role == "tool":
            cid = message.get("tool_call_id")
            if cid not in pending:
                raise MessageError("Orphan or duplicate tool result")
            pending.remove(cid)
            item["tool_call_id"] = cid
            if not isinstance(item["content"], str):
                raise MessageError("Tool results must be text; put observations in a user message")
        if isinstance(item["content"], list):
            for part in item["content"]:
                if part.get("type") == "image_url":
                    if role != "user" or not vision:
                        raise MessageError(
                            "This model requires supported images in user messages",
                            category="configuration",
                        )
                    image_url = part.get("image_url", {}).get("url", "")
                    if image_prefixes is not None and not image_url.startswith(image_prefixes):
                        raise MessageError(
                            "Images require an accepted data URL or file reference",
                            category="configuration",
                        )
        output.append(item)
    if pending:
        raise MessageError("Cannot send a request with unfinished tool calls")
    return output


def normalize_chat_response(response: Any, *, forbid_tools=False) -> Any:
    from types import SimpleNamespace

    choices = getattr(response, "choices", None)
    if not choices:
        raise MessageError("API returned no completion choices")
    choice = choices[0]
    finish = getattr(choice, "finish_reason", None)
    if finish not in {"stop", "tool_calls"}:
        category = "capacity" if finish == "length" else "response"
        raise MessageError(
            f"API generation incomplete: finish_reason={finish!r}; no action executed",
            category=category,
        )
    message = getattr(choice, "message", None)
    if message is None or not (isinstance(message, dict) or hasattr(message, "__dict__")):
        raise MessageError("API returned no assistant message")
    raw = (
        message.model_dump(exclude_unset=True)
        if hasattr(message, "model_dump")
        else dict(message)
        if isinstance(message, dict)
        else vars(message)
    )
    native = {
        key: copy.deepcopy(raw[key])
        for key in ("role", "content", "reasoning_content", "tool_calls")
        if key in raw
    }
    native["role"] = "assistant"
    native.setdefault("content", None)
    for key in ("content", "reasoning_content"):
        if native.get(key) is not None and not isinstance(native[key], str):
            raise MessageError(f"API returned non-text assistant {key}")
    calls, serialized, ids = [], [], set()
    raw_calls = raw.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise MessageError("API tool_calls must be a list")
    for call in raw_calls:
        if not (isinstance(call, dict) or hasattr(call, "__dict__")):
            raise MessageError("API returned an invalid function call")
        call = (
            call
            if isinstance(call, dict)
            else (call.model_dump() if hasattr(call, "model_dump") else vars(call))
        )
        fn = call.get("function", {})
        if not (isinstance(fn, dict) or hasattr(fn, "__dict__")):
            raise MessageError("API returned an invalid function body")
        fn = fn if isinstance(fn, dict) else vars(fn)
        cid = call.get("id")
        if (
            not isinstance(cid, str)
            or not cid
            or cid in ids
            or call.get("type") != "function"
            or not isinstance(fn.get("name"), str)
            or not isinstance(fn.get("arguments"), str)
        ):
            raise MessageError("API returned an incomplete/duplicate function call")
        ids.add(cid)
        serialized.append(
            {
                "id": cid,
                "type": "function",
                "function": {"name": fn["name"], "arguments": fn["arguments"]},
            }
        )
        calls.append(
            SimpleNamespace(
                id=cid, type="function", function=SimpleNamespace(**serialized[-1]["function"])
            )
        )
    if (forbid_tools and calls) or (finish == "tool_calls" and not calls):
        raise MessageError("API response violates the requested tool protocol")
    if calls:
        native["tool_calls"] = serialized
    return SimpleNamespace(
        content=native["content"], tool_calls=calls or None,
        request_message=native, reasoning_content=native.get("reasoning_content"),
        finish_reason=finish, usage=getattr(response, "usage", None),
        model=getattr(response, "model", None),
    )


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
