"""Provider wire contracts and robot-loop history, with no network or simulator."""

from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from roborsi.embodied.agent_loop import messages as history
from roborsi.embodied.agent_loop import vlm_io
from roborsi.evaluation.suite import _api_runtime_settings

MODELS = ["deepseek/deepseek-flash", "moonshot/kimi-k3"]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "parameters": {
                "type": "object",
                "properties": {"success": {"type": "boolean"}},
                "required": ["success"],
            },
        },
    },
]
INITIAL = [
    {"role": "system", "content": "Use visible evidence."},
    {"role": "user", "content": "Move the block."},
]


def call(name="move", args='{"x":1}', cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def response(content="", reasoning="original reasoning", calls=None, finish=None):
    message = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish or ("tool_calls" if calls else "stop"),
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    }


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("ROBORSI_API_", "DEEPSEEK_", "MOONSHOT_")) or name in {
            "ROBORSI_REASONING_EFFORT",
            "ROBORSI_DEEPSEEK_THINKING",
            "ROBORSI_PERCEPTION_MODEL",
            "ROBORSI_GATE_MODEL",
            "ROBORSI_ENGINEER_MODEL",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-moonshot-key")


@pytest.fixture
def wire(monkeypatch):
    import openai

    original = openai.OpenAI
    queued, requests, clients = [], [], []

    def handle(request):
        requests.append((request, json.loads(request.content)))
        item = queued.pop(0)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "test failure", "type": "test"}})
        return httpx.Response(200, json=item)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        client = original(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        clients.append(client)
        return client

    monkeypatch.setattr(openai, "OpenAI", factory)
    monkeypatch.setattr(vlm_io.time, "sleep", lambda _: None)
    return SimpleNamespace(queued=queued, requests=requests, clients=clients)


@pytest.mark.parametrize("model", MODELS)
def test_wire_preserves_reasoning_raw_arguments_and_usage(model, wire):
    raw_args = '{ "x" : 2 }'
    wire.queued.extend(
        [response(None, "reason A", [call(args=raw_args)]), response("finished", "reason B")]
    )
    original = copy.deepcopy(INITIAL)
    with vlm_io.capture_usage() as usage:
        reply = vlm_io._call_vlm_tools(model, original, TOOLS)
        saved = history._assistant_tool_calls_msg(reply, reply.tool_calls)
        convo = original + [saved, {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}]
        vlm_io._call_vlm_tools(model, convo, TOOLS, tool_choice="none")
    first, second = [body for _, body in wire.requests]
    assert original == INITIAL
    assert saved["content"] is None
    assert saved["reasoning_content"] == "reason A"
    assert saved["tool_calls"][0]["function"]["arguments"] == raw_args
    assert second["messages"][2] == saved
    assert second["tools"] == TOOLS and second["tool_choice"] == "none"
    assert (
        not {
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "stop",
            "thinking_budget",
        }
        & first.keys()
    )
    if model.startswith("deepseek"):
        assert first["thinking"] == {"type": "enabled"}
        assert first["max_tokens"] == 16384
        assert "max_completion_tokens" not in first
        assert wire.requests[0][0].url.host == "api.deepseek.com"
    else:
        assert first["max_completion_tokens"] == 16384
        assert "max_tokens" not in first and "thinking" not in first
        assert wire.requests[0][0].url.host == "api.moonshot.ai"
    assert usage.vlm_calls == 2 and usage.total_tokens == 22
    assert all(client.is_closed() for client in wire.clients)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("reason", ["reason", "", None])
def test_text_only_retry_preserves_original_fields(model, reason, wire):
    wire.queued.extend([response("```python\nx = 1\n```", reason), response("repaired", "next")])
    reply = vlm_io._call_vlm_no_tools(model, INITIAL)
    assert isinstance(reply, str)
    saved = history.serialize_assistant_message(reply)
    assert saved["reasoning_content"] == reason
    vlm_io._call_vlm_no_tools(model, INITIAL + [saved, {"role": "user", "content": "Repair."}])
    assert wire.requests[-1][1]["messages"][2] == saved


def test_unknown_models_keys_and_invalid_effort_fail_before_network(monkeypatch, wire):
    with pytest.raises(vlm_io.ProviderError, match="Unsupported"):
        vlm_io._call_vlm_tools("moonshot/kimi-unknown", INITIAL, TOOLS)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(vlm_io.ProviderError, match="DEEPSEEK_API_KEY"):
        vlm_io.resolve_api_config(MODELS[0])
    monkeypatch.setenv("ROBORSI_API_REASONING_EFFORT", "medium")
    with pytest.raises(vlm_io.ProviderError, match="reasoning effort"):
        vlm_io.resolve_api_config(MODELS[1])
    assert not wire.requests


@pytest.mark.parametrize("model,route", [
    ("openai/gpt-5.4", "openai"),
    ("azure/deployment-name", "openai"),
    ("azure-openai/deployment-name", "openai"),
    ("gpt-5.4", "openai"),
    ("o3", "openai"),
    ("o4-mini", "openai"),
    ("anthropic/claude-sonnet-4-6", "anthropic"),
    ("claude-sonnet-4-6", "anthropic"),
    ("legacy-deployment-name", "anthropic"),
    ("deepseek/deepseek-flash", "chat"),
    ("moonshot/kimi-k3", "chat"),
    ("kimi/kimi-k3", "chat"),
])
def test_model_prefix_routes_tools_and_text_with_original_request_options(model, route, monkeypatch):
    calls, retries = [], []
    reply = SimpleNamespace(content="reply", tool_calls=None)

    def handler(selected):
        def request(*args, **kwargs):
            calls.append((selected, args, kwargs))
            return reply
        return request

    def retry(request):
        retries.append(True)
        return request()

    monkeypatch.setattr(vlm_io, "_openai_call_with_tools", handler("openai"))
    monkeypatch.setattr(vlm_io, "_anthropic_call_with_tools", handler("anthropic"))
    monkeypatch.setattr(vlm_io, "_chat_call_with_tools", handler("chat"))
    monkeypatch.setattr(vlm_io, "_retry_litellm", retry)
    prefill = "{" if route == "anthropic" else None
    assert vlm_io._call_vlm_tools(
        model, INITIAL, TOOLS, thinking_budget=4000, tool_choice="none",
        prefill=prefill, request_kind="reviewer",
    ) is reply
    assert vlm_io._call_vlm_no_tools(model, INITIAL) == "reply"
    assert [selected for selected, _, _ in calls] == [route, route]
    assert calls[0][1] == (model, INITIAL, [] if route == "openai" else TOOLS)
    assert calls[1][1] == (model, INITIAL, [])
    if route == "chat":
        assert calls[0][2] == {"tool_choice": "none", "purpose": "reviewer", "prefill": None}
        assert calls[1][2] == {"tool_choice": None, "purpose": "text", "prefill": None}
        assert not retries  # The Chat handler owns its bounded retry loop.
    elif route == "anthropic":
        assert calls[0][2] == {
            "thinking_budget": 4000, "tool_choice_override": "none", "prefill": "{",
        }
        assert calls[1][2] == {
            "thinking_budget": 0, "tool_choice_override": None, "prefill": None,
        }
        assert len(retries) == 2
    else:
        assert calls[0][2] == calls[1][2] == {}
        assert len(retries) == 2


@pytest.mark.parametrize("model", MODELS)
def test_chat_prefill_rejected_before_request(model, wire):
    with pytest.raises(vlm_io.ProviderError, match="prefill") as exc:
        vlm_io._call_vlm_tools(model, INITIAL, TOOLS, prefill="{")
    assert exc.value.category == "configuration" and not wire.requests


@pytest.mark.parametrize("model,override,route", [
    ("openai/gpt-5.4", "anthropic", "openai"),
    ("azure/deployment-name", "anthropic", "openai"),
    ("anthropic/claude-sonnet-4-6", "anthropic", "anthropic"),
    ("anthropic/claude-sonnet-4-6", "openai", "anthropic"),
    ("deepseek/deepseek-flash", "openai", "chat"),
    ("moonshot/kimi-k3", "openai", "chat"),
])
@pytest.mark.parametrize("encoding,suffix,mime", [
    ("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"),
    ("PNG", ".jpg", "image/png"),
])
def test_explicit_perception_model_determines_image_route(
    model, override, route, encoding, suffix, mime, monkeypatch, tmp_path
):
    path = tmp_path / f"view{suffix}"
    Image.new("RGB", (8, 8)).save(path, format=encoding)
    expected_url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    monkeypatch.setenv("ROBORSI_PERCEPTION_MODEL", model)
    monkeypatch.setenv("ROBORSI_VLM_PROVIDER", override)
    calls = []

    def openai_text(actual_model, system, messages):
        calls.append(("openai", actual_model))
        assert messages[0]["content"][1]["image_url"]["url"] == expected_url
        return "image reply"

    def chat_text(actual_model, messages, *, request_kind):
        calls.append(("chat", actual_model))
        assert request_kind == "perception"
        assert messages[1]["content"][1]["image_url"]["url"] == expected_url
        return "image reply"

    def anthropic_image(**kwargs):
        calls.append(("anthropic", kwargs["model"]))
        source = kwargs["messages"][0]["content"][1]["source"]
        assert source["media_type"] == mime
        assert base64.b64decode(source["data"]) == path.read_bytes()
        assert kwargs["max_tokens"] == 512
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="image reply")])

    monkeypatch.setattr(vlm_io, "_vlm_complete_openai", openai_text)
    monkeypatch.setattr(vlm_io, "_call_vlm_no_tools", chat_text)
    monkeypatch.setattr(vlm_io, "_anthropic_client", lambda: SimpleNamespace(
        messages=SimpleNamespace(create=anthropic_image),
    ))
    assert vlm_io._call_vlm_image(model, "system", "locate", path) == "image reply"
    assert calls == [(route, model.split("/", 1)[-1] if route == "anthropic" else model)]


def test_parameters_do_not_mutate_schemas_or_openai_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.example/v1")
    monkeypatch.setenv("ROBORSI_API_REASONING_EFFORT", "xhigh")
    cfg = vlm_io.resolve_api_config(MODELS[0])
    schema = [
        {
            "type": "function",
            "function": {
                "name": "any_array",
                "parameters": {"type": "object", "properties": {"x": {"type": "array"}}},
            },
        }
    ]
    original = copy.deepcopy(schema)
    kwargs = vlm_io._chat_request(cfg, INITIAL, schema)
    assert kwargs["tools"] == original == schema
    assert cfg.reasoning_effort == "high"
    assert cfg.base_url == "https://api.deepseek.com"
    assert "test-deepseek-key" not in repr(cfg)
    with pytest.raises(vlm_io.ProviderError, match="required"):
        vlm_io._chat_request(cfg, INITIAL, TOOLS, "required")
    monkeypatch.setenv("ROBORSI_DEEPSEEK_THINKING", "disabled")
    kwargs = vlm_io._chat_request(vlm_io.resolve_api_config(MODELS[0]), INITIAL, TOOLS, "required")
    assert "reasoning_effort" not in kwargs
    assert kwargs["extra_body"]["thinking"]["type"] == "disabled"


@pytest.mark.parametrize(
    "status, expected",
    [(401, "configuration"), (403, "configuration"), (429, "rate_limit"), (503, "transport")],
)
def test_bounded_failure_retries_and_classification(status, expected, wire, monkeypatch):
    from roborsi.evaluation.atomic import classify_attempt_exception

    monkeypatch.setenv("ROBORSI_API_ATTEMPTS", "2")
    wire.queued.extend([status, status])
    with pytest.raises(vlm_io.ProviderError) as failure:
        vlm_io._call_vlm_tools(MODELS[0], INITIAL, TOOLS)
    assert failure.value.category == expected
    assert len(wire.requests) == (2 if status in {429, 503} else 1)
    assert classify_attempt_exception(failure.value) == (
        "infra" if status in {429, 503} else "implementation_error"
    )


@pytest.mark.parametrize(
    "finish", ["length", "content_filter", "insufficient_system_resource", "aborted", None]
)
def test_incomplete_responses_never_return_executable_calls(finish, wire):
    payload = response(calls=[call()])
    payload["choices"][0]["finish_reason"] = finish
    wire.queued.append(payload)
    with pytest.raises(vlm_io.ProviderError, match="incomplete"):
        vlm_io._call_vlm_tools(MODELS[0], INITIAL, TOOLS)


def test_missing_reasoning_and_forbidden_tools_fail_before_dispatch(wire):
    payload = response(calls=[call()])
    del payload["choices"][0]["message"]["reasoning_content"]
    wire.queued.extend([payload, response(calls=[call()])])
    with pytest.raises(vlm_io.ProviderError, match="reasoning_content"):
        vlm_io._call_vlm_tools(MODELS[0], INITIAL, TOOLS)
    with pytest.raises(vlm_io.ProviderError, match="protocol"):
        vlm_io._call_vlm_tools(MODELS[1], INITIAL, TOOLS, tool_choice="none")


@pytest.mark.parametrize("continuations", [1, 2])
def test_compaction_discards_old_reasoning_and_preserves_complete_groups(continuations):
    convo = copy.deepcopy(INITIAL)
    for i in range(12):
        convo += [
            {
                "role": "assistant",
                "content": f"prose-{i}",
                "reasoning_content": f"reason-{i}",
                "tool_calls": [call(cid=f"a{i}"), call(cid=f"b{i}")],
            },
            {"role": "tool", "tool_call_id": f"a{i}", "content": f"result-a-{i}"},
            {"role": "tool", "tool_call_id": f"b{i}", "content": f"result-b-{i}"},
        ]
    # Cut on either the first or the last result of a parallel call group.
    convo.extend({"role": "user", "content": "Continue."} for _ in range(continuations))
    saved = copy.deepcopy(convo)
    compact = history._summarize_old_trace(convo)
    summary = compact[2]
    assert convo == saved
    assert summary["role"] == "user" and set(summary) == {"role", "content"}
    assert "move(x=1)" in summary["content"] and "result-a-0" in summary["content"]
    assert "reason-" not in summary["content"] and "prose-" not in summary["content"]
    assert compact[3:] == convo[26:]
    for model in MODELS:
        assert vlm_io._chat_request(vlm_io.resolve_api_config(model), compact, TOOLS)["messages"]
    # A half-completed group is pinned, even if that exceeds the recent window.
    unfinished = copy.deepcopy(INITIAL) + [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "pending",
            "tool_calls": [call(cid=f"p{i}") for i in range(20)],
        }
    ]
    unfinished += [{"role": "tool", "tool_call_id": f"p{i}", "content": "ok"} for i in range(19)]
    assert history._summarize_old_trace(unfinished) == unfinished
    with pytest.raises(vlm_io.ProviderError, match="unfinished"):
        vlm_io._chat_request(vlm_io.resolve_api_config(MODELS[1]), unfinished, TOOLS)["messages"]


@pytest.mark.parametrize("result_count", [19, 20])
def test_compaction_keeps_large_groups_and_summarizes_only_older_groups(result_count):
    older = [
        {"role": "assistant", "content": None, "tool_calls": [call(cid="older")]},
        {"role": "tool", "tool_call_id": "older", "content": "older result"},
    ]
    recent = [
        {
            "role": "assistant",
            "content": "current group",
            "tool_calls": [call(cid=f"large-{i}") for i in range(20)],
        }
    ] + [
        {"role": "tool", "tool_call_id": f"large-{i}", "content": f"recent-{i}"}
        for i in range(result_count)
    ]
    convo = copy.deepcopy(INITIAL) + older + recent
    compact = history._summarize_old_trace(convo)
    assert compact[:2] == INITIAL
    assert "older result" in compact[2]["content"]
    assert "recent-" not in compact[2]["content"]
    assert compact[3:] == recent  # Entire large group, including an unfinished one.


@pytest.mark.parametrize("model", ["gpt-5.4", "azure/gpt-5.4", "anthropic/claude-sonnet-4-6"])
def test_legacy_rollout_compaction_preserves_groups_through_conversion(model, monkeypatch, tmp_path):
    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    prior = copy.deepcopy(INITIAL)
    for i in range(12):
        prior.extend([
            {"role": "assistant", "content": f"prose-{i}",
             "tool_calls": [call(cid=f"a{i}"), call(cid=f"b{i}")]},
            {"role": "tool", "tool_call_id": f"a{i}", "content": f"result-a-{i}"},
            {"role": "tool", "tool_call_id": f"b{i}", "content": f"result-b-{i}"},
        ])
    saved = copy.deepcopy(prior)
    sent, unhooked = [], []
    env = SimpleNamespace(
        backend_name="libero-pro",
        take_snapshot=lambda: Observation(),
        tool_handlers=lambda: {},
        check_success=lambda: False,
        hook_physics_step=lambda _: lambda: unhooked.append(True),
    )

    def model_reply(actual_model, messages, tools):
        assert actual_model == model
        sent.append(copy.deepcopy(messages))
        return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            id="done", type="function",
            function=SimpleNamespace(name="done", arguments='{"success":false}'),
        )])

    monkeypatch.setattr(rollout, "_call_vlm_tools", model_reply)
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    with use_run_mode("frozen"):
        rollout.run_rollout(
            env, seed=0, task_name="demo", instruction="continue",
            expected_on_success="moved", model=model, tool_budget=1,
            workdir=tmp_path, restrict_to_names=set(), prior_messages=prior,
        )
    assert prior == saved and unhooked == [True] and len(sent) == 1
    compact = sent[0]
    assert compact[:2] == INITIAL
    assert compact[2]["role"] == "user" and "result-a-0" in compact[2]["content"]
    assert "prose-" not in compact[2]["content"]
    # The appended subtask makes the nominal cut land on result a8.
    # Keep assistant(a8,b8) and both results instead of summarizing that group.
    assert compact[3:-1] == prior[26:]
    expected_ids = [f"{prefix}{i}" for i in range(8, 12) for prefix in ("a", "b")]
    if model.startswith("anthropic/"):
        native = history._convert_messages_to_anthropic(compact)
        blocks = [b for m in native if isinstance(m["content"], list) for b in m["content"]]
        assert [b["id"] for b in blocks if b["type"] == "tool_use"] == expected_ids
        assert [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"] == expected_ids
    else:
        _, items = vlm_io._messages_to_responses_input(compact)
        assert [m["call_id"] for m in items if m.get("type") == "function_call"] == expected_ids
        assert [m["call_id"] for m in items if m.get("type") == "function_call_output"] == expected_ids


def test_image_is_a_user_message_with_actual_mime(tmp_path):
    path = tmp_path / "misnamed.jpg"
    Image.new("RGB", (8, 8)).save(path, format="PNG")
    convo = copy.deepcopy(INITIAL) + [
        {"role": "assistant", "content": None, "reasoning_content": "look", "tool_calls": [call()]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    history._append_image(convo, path)
    assert convo[-2]["content"] == "ok" and convo[-1]["role"] == "user"
    assert convo[-1]["content"][-1]["image_url"]["url"].startswith("data:image/png;base64,")
    vlm_io._chat_request(vlm_io.resolve_api_config(MODELS[1]), convo, TOOLS)["messages"]
    with pytest.raises(vlm_io.ProviderError, match="supported images"):
        vlm_io._chat_request(vlm_io.resolve_api_config("deepseek/deepseek-v4-pro"), convo, TOOLS)["messages"]


def _image_bytes(messages):
    return [
        base64.b64decode(block["image_url"]["url"].split(",", 1)[1])
        for message in messages if isinstance(message.get("content"), list)
        for block in message["content"] if block.get("type") == "image_url"
    ]


@pytest.fixture
def image_rollout(monkeypatch, tmp_path):
    """Use the real LIBERO look, dispatcher and loop; fake only model/physics."""
    import numpy as np

    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.embodied.skills.base.look.libero.policy import dispatch_runtime as look
    from roborsi.runtime_mode import use_run_mode

    phase, saved, requests, states = [0], [], [], []

    def snapshot():
        colors = ("red", "blue") if phase[0] == 0 else ("green", "yellow")
        return Observation(images={
            camera: np.asarray(Image.new("RGB", (24, 24), color))
            for camera, color in zip(("head_camera", "wrist"), colors)
        })

    def observe(state, args):
        result = look(state, args)
        saved.append(state.last_image_path.read_bytes())
        states.append(state)
        return result

    def move(state, args):
        phase[0] += 1
        return {"ok": True}, snapshot()

    env = SimpleNamespace(
        backend_name="libero-pro", take_snapshot=snapshot,
        sensor_generation=lambda: (0, phase[0]),
        tool_handlers=lambda: {"look": observe, "grasp_object": move},
        check_success=lambda: False, hook_physics_step=lambda _: lambda: None,
    )
    monkeypatch.setattr(rollout, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(rollout, "_dispatch_meta_tool", lambda *a, **kw: None)

    def run(model, rounds):
        replies = iter(rounds)

        def request(actual_model, messages, tools):
            requests.append(copy.deepcopy(messages))
            calls = [
                call(name, json.dumps(args), f"c{len(requests)}-{i}")
                for i, (name, args) in enumerate(next(replies))
            ]
            reply = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=c["id"], function=SimpleNamespace(**c["function"]))
                for c in calls
            ])
            if vlm_io.is_compatible_model(model):
                reply.request_message = {
                    "role": "assistant", "content": None,
                    "reasoning_content": f"retained reasoning {len(requests)}", "tool_calls": calls,
                }
            return reply

        monkeypatch.setattr(rollout, "_call_vlm_tools", request)
        with use_run_mode("frozen"):
            return rollout.run_rollout(
                env, seed=0, task_name="images", instruction="pick cream cheese",
                expected_on_success="in basket", model=model, tool_budget=len(rounds),
                workdir=tmp_path, restrict_to_names=set(),
            )

    return SimpleNamespace(run=run, env=env, saved=saved, requests=requests, states=states)


@pytest.mark.parametrize("model", ["gpt-5.4", "azure/gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS])
@pytest.mark.parametrize("scenario", [
    "two_views", "same_path", "same_path_one_batch", "no_refresh",
    "repeat_view", "post_action_two_views", "post_action_one_view", "aliases", "stale_batch",
])
def test_rollout_preserves_observations_across_providers(model, scenario, image_rollout):
    head = ("look", {"camera": "head"})
    wrist = ("look", {"camera": "wrist"})
    move = ("grasp_object", {"object": "cream cheese"})
    done = ("done", {"success": False})
    rounds, selected = {
        "two_views": ([[head, wrist], [done]], [0, 1]),
        "same_path": ([[head], [move], [head], [done]], [0, 1]),
        "same_path_one_batch": ([[head, move, head], [done]], [1]),
        "no_refresh": ([[head], [move], [done]], [0]),
        "repeat_view": ([[head, wrist, head], [done]], [1, 2]),
        "post_action_two_views": ([[head, wrist, move, head, wrist], [done]], [2, 3]),
        "post_action_one_view": ([[head, move, wrist], [done]], [1]),
        "aliases": ([[head, ("look", {}), ("look", {"camera": "agentview"})], [done]], [2]),
        "stale_batch": ([[head, move], [done]], [0]),
    }[scenario]
    result = image_rollout.run(model, rounds)
    messages = image_rollout.requests[-1]
    expected = [image_rollout.saved[index] for index in selected]
    assert _image_bytes(messages) == expected
    assert _image_bytes(result.messages) == expected
    # Every requested look still executes; selection must not change the
    # observation strategy, tool count or the original text results.
    assert len(image_rollout.saved) == sum(name == "look" for batch in rounds for name, _ in batch)
    assert len([m for m in messages if m["role"] == "tool"]) == sum(len(b) for b in rounds[:-1])
    if scenario == "same_path":
        assert _image_bytes(image_rollout.requests[2]) == image_rollout.saved[:1]
        assert messages[:len(image_rollout.requests[1])] == image_rollout.requests[1]
    if scenario == "stale_batch":
        assert "not a fresh observation" in json.dumps(messages)
    assert image_rollout.states[-1].last_image_path.is_file()
    assert image_rollout.states[-1]._pending_images == []
    assert all(isinstance(m["content"], str) for m in messages if m["role"] == "tool")
    history.prepare_chat_messages(messages, require_reasoning=vlm_io.is_compatible_model(model))

    # Check that native provider conversion still carries the actual image data.
    if vlm_io.is_compatible_model(model):
        body = vlm_io._chat_request(vlm_io.resolve_api_config(model), messages, TOOLS)
        assert _image_bytes(body["messages"]) == expected
    elif model.startswith("anthropic/"):
        native = history._convert_messages_to_anthropic(messages)
        images = [b for m in native if isinstance(m["content"], list)
                  for b in m["content"] if b.get("type") == "image"]
        assert [base64.b64decode(b["source"]["data"]) for b in images] == expected
    else:
        _, items = vlm_io._messages_to_responses_input(messages)
        images = [b for item in items if isinstance(item.get("content"), list)
                  for b in item["content"] if b.get("type") == "input_image"]
        assert [base64.b64decode(b["image_url"].split(",", 1)[1]) for b in images] == expected


@pytest.mark.parametrize("model", ["gpt-5.4", MODELS[1]])
@pytest.mark.parametrize("ending", ["done", "budget"])
def test_final_tool_batch_keeps_images_and_complete_results(model, ending, image_rollout):
    head = ("look", {"camera": "head"})
    wrist = ("look", {"camera": "wrist"})
    batch = [head, ("done", {"success": False}), wrist] if ending == "done" else [head, wrist]
    result = image_rollout.run(model, [batch])
    assert len(image_rollout.requests) == 1
    assert len(image_rollout.saved) == (1 if ending == "done" else 2)
    assert _image_bytes(result.messages) == image_rollout.saved
    history.prepare_chat_messages(result.messages)
    if ending == "done":
        tools = [m for m in result.messages if m["role"] == "tool"]
        assert json.loads(tools[-1]["content"])["skipped"] is True
    assert result.messages[-1]["role"] == "user"


def test_image_snapshot_survives_overwrite_with_unchanged_mtime(tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext

    path = tmp_path / "fixed.jpg"
    state = DispatchContext(env=None, workdir=tmp_path, last_image_path=path)
    Image.new("RGB", (8, 8), "red").save(path, format="PNG")
    stamp = path.stat().st_mtime_ns
    first = path.read_bytes()
    history._capture_tool_image(state, "look", {"camera": "head"})
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    original_history = copy.deepcopy(convo)
    Image.new("RGB", (8, 8), "green").save(path, format="PNG")
    os.utime(path, ns=(stamp, stamp))
    second = path.read_bytes()
    history._capture_tool_image(state, "look", {"camera": "head"})
    history._capture_tool_image(state, "grasp_object")  # unchanged file: no duplicate
    path.unlink()  # publishing uses captured bytes, not a later file read
    history._append_pending_images(convo, state)
    history._append_pending_images(convo, state)
    assert len(convo) == 4 and _image_bytes(convo) == [first, second]
    assert convo[:3] == original_history
    assert state.last_image_path == path
    assert all(b["image_url"]["url"].startswith("data:image/png;")
               for b in convo[-1]["content"] if b["type"] == "image_url")


def test_successful_look_updates_state_even_when_pixels_and_mtime_are_identical(tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext

    phase = [0]
    env = SimpleNamespace(sensor_generation=lambda: (0, phase[0]))
    path = tmp_path / "fixed.png"
    Image.new("RGB", (8, 8), "blue").save(path)
    state = DispatchContext(env=env, workdir=tmp_path, last_image_path=path)
    result = {"ok": True, "camera": "head_camera"}
    history._capture_tool_image(state, "look", result=result)
    phase[0] = 1
    history._capture_tool_image(state, "grasp_object", result={"ok": True})
    assert state._image_sequence == 1  # motion alone did not acquire an image
    history._capture_tool_image(state, "look", result=result)
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    assert _image_bytes(convo) == [path.read_bytes()]
    assert "Observation 2:" in json.dumps(convo[-1])
    assert "state=(0, 1)" in json.dumps(convo[-1])
    assert "not a fresh observation" not in json.dumps(convo[-1])


def test_latest_camera_does_not_replace_requested_crops_and_markers(image_rollout, tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext, _dispatch
    from roborsi.embodied.sim.robotwin.robotwin_perceive_tools import (
        _do_label_points_grid,
        _do_zoom_in,
    )

    state = DispatchContext(env=image_rollout.env, workdir=tmp_path, last_image_path=None, ns="libero")
    state._tool_handlers = {**image_rollout.env.tool_handlers(),
                            "zoom_in": _do_zoom_in, "label_points_grid": _do_label_points_grid}
    _dispatch(state, {"tool": "look", "args": {"camera": "head"}})
    _dispatch(state, {"tool": "zoom_in", "args": {"u": 12, "v": 12}})
    crop = state.last_image_path.read_bytes()
    crop_record = state._pending_images[-1]
    assert crop_record["camera"] == "head_camera" and crop_record["source"] == 1
    _dispatch(state, {"tool": "label_points_grid", "args": {"grid_n": 2, "margin_px": 4}})
    marked = state.last_image_path.read_bytes()
    _dispatch(state, {"tool": "look", "args": {"camera": "head"}})
    latest = state.last_image_path.read_bytes()
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    assert _image_bytes(convo) == [crop, marked, latest]
    labels = json.dumps(convo[-1])
    assert "kind=crop" in labels and "kind=annotation" in labels
    assert "source_observation=1" in labels and "camera=head_camera" in labels


def test_crop_inherits_source_state_instead_of_becoming_a_new_observation(image_rollout, tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext, _dispatch
    from roborsi.embodied.sim.robotwin.robotwin_perceive_tools import _do_zoom_in

    state = DispatchContext(env=image_rollout.env, workdir=tmp_path, last_image_path=None, ns="libero")
    state._tool_handlers = {**image_rollout.env.tool_handlers(), "zoom_in": _do_zoom_in}
    _dispatch(state, {"tool": "look", "args": {"camera": "head"}})
    before = state.last_image_path.read_bytes()
    # Publish the source first: provenance must survive draining the queue.
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    original = copy.deepcopy(convo)
    _dispatch(state, {"tool": "grasp_object"})
    _dispatch(state, {"tool": "zoom_in", "args": {"u": 12, "v": 12}})
    crop = state.last_image_path.read_bytes()
    history._append_pending_images(convo, state)
    assert convo[:len(original)] == original
    assert _image_bytes(convo) == [before, crop]
    assert "state=(0, 0)" in json.dumps(convo[-1])
    assert "not a fresh observation" in json.dumps(convo[-1])


def test_orbit_views_keep_contact_sheet_and_distinct_point_annotations(image_rollout, tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext, _dispatch
    from roborsi.embodied.skills.base.mark_orbit_point.libero.policy import dispatch_runtime as mark
    from roborsi.embodied.skills.base.observe_orbit.libero.policy import dispatch_runtime as orbit

    env = image_rollout.env
    rgb = env.take_snapshot().images["head_camera"]
    frames = {name: SimpleNamespace(name=name, rgb=rgb) for name in ("left", "right")}
    env.capture_orbit_views = lambda **kw: frames
    env.orbit_frame = frames.get
    env.orbit_pixel_to_world = lambda *a: [0.1, 0.2, 0.3]
    state = DispatchContext(env=env, workdir=tmp_path, last_image_path=None, ns="libero")
    state._tool_handlers = {"observe_orbit": orbit, "mark_orbit_point": mark}
    saved = []
    for name, args in [
        ("observe_orbit", {}),
        ("observe_orbit", {"view": "left"}),
        ("mark_orbit_point", {"view": "left", "u": 8, "v": 8}),
        ("mark_orbit_point", {"view": "left", "u": 16, "v": 16}),
        ("observe_orbit", {"view": " left "}),
    ]:
        result, _ = _dispatch(state, {"tool": name, "args": args})
        assert result["ok"]
        saved.append(state.last_image_path.read_bytes())
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    assert _image_bytes(convo) == [saved[i] for i in (0, 2, 3, 4)]
    labels = json.dumps(convo[-1])
    assert "kind=contact_sheet" in labels and "views=['left', 'right']" in labels
    assert labels.count("kind=annotation") == 2
    assert "camera=orbit/left" in labels


@pytest.mark.parametrize("tool", ["preview_move_to_pose", "custom_visual_skill"])
def test_preview_and_unfamiliar_visual_outputs_are_not_deduplicated_as_camera_frames(tool, tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext

    state = DispatchContext(env=None, workdir=tmp_path, last_image_path=None)
    saved = []
    for index, (name, color) in enumerate([(tool, "red"), (tool, "green"), ("look", "blue")]):
        state.last_image_path = tmp_path / f"image-{index}.png"
        Image.new("RGB", (8, 8), color).save(state.last_image_path)
        saved.append(state.last_image_path.read_bytes())
        history._capture_tool_image(state, name, result={"camera": "head_camera"})
    convo = copy.deepcopy(INITIAL)
    history._append_pending_images(convo, state)
    assert _image_bytes(convo) == saved
    if tool == "custom_visual_skill":
        assert "capture state is unspecified" in json.dumps(convo[-1])


@pytest.mark.parametrize("advance", [False, True])
def test_physics_fallback_tracks_nested_tools_and_preserves_existing_hook(advance, tmp_path):
    import numpy as np

    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.embodied.agent_loop.rollout import DispatchContext, _dispatch, _dispatch_tool
    from roborsi.embodied.sim.robotwin.adapter import RoboTwinEnv
    from roborsi.embodied.sim.robotwin.robotwin_perceive_tools import _do_look, _do_scan_wrist

    ticks, trajectory = [], []
    original_step = lambda: ticks.append(1)  # noqa: E731
    scene = SimpleNamespace(step=original_step)
    rgb = np.asarray(Image.new("RGB", (8, 8), "blue"))
    obs = Observation(images={"head_camera": rgb, "right_camera": rgb})
    env = SimpleNamespace(_impl=SimpleNamespace(scene=scene), take_snapshot=lambda: obs)
    env.hook_physics_step = lambda callback: RoboTwinEnv.hook_physics_step(env, callback)
    outer_unhook = env.hook_physics_step(lambda: trajectory.append(1))
    outer_step = scene.step

    def maybe_move(state, args):
        if advance:
            scene.step()
        return {"ok": False, "reason": "partial move" if advance else "refused without moving"}, obs

    def inspect(state, args):
        return _dispatch_tool(state, "move_to_pose")

    state = DispatchContext(env=env, workdir=tmp_path, last_image_path=None)
    state._tool_handlers = {"look": _do_look, "scan_wrist": _do_scan_wrist,
                            "inspect": inspect, "move_to_pose": maybe_move}
    try:
        _dispatch(state, {"tool": "look"})
        head = state.last_image_path.read_bytes()
        _dispatch(state, {"tool": "inspect"})  # name/category does not reveal the nested motion
        _dispatch(state, {"tool": "look", "args": {"camera": "right_camera"}})
        _dispatch(state, {"tool": "scan_wrist", "args": {"arm": "right"}})
        wrist = state.last_image_path.read_bytes()
        assert scene.step is outer_step and state._image_step_hook_active is False
        assert len(ticks) == len(trajectory) == state._image_physics_steps == int(advance)
        convo = copy.deepcopy(INITIAL)
        history._append_pending_images(convo, state)
        assert _image_bytes(convo) == ([wrist] if advance else [head, wrist])
        assert json.dumps(convo[-1]).count("camera=right_camera") == 1
    finally:
        outer_unhook()
    assert scene.step is original_step


def test_observation_step_hook_is_restored_when_tool_raises(tmp_path):
    from roborsi.embodied.agent_loop.rollout import DispatchContext, _dispatch
    from roborsi.embodied.sim.robotwin.adapter import RoboTwinEnv

    def original_step():
        return None

    scene = SimpleNamespace(step=original_step)
    env = SimpleNamespace(_impl=SimpleNamespace(scene=scene))
    env.hook_physics_step = lambda callback: RoboTwinEnv.hook_physics_step(env, callback)

    def fail(state, args):
        scene.step()
        raise ValueError("tool failed after motion")

    state = DispatchContext(env=env, workdir=tmp_path, last_image_path=None)
    state._tool_handlers = {"broken": fail}
    with pytest.raises(ValueError, match="tool failed"):
        _dispatch(state, {"tool": "broken"})
    assert scene.step is original_step and state._image_step_hook_active is False
    assert state._image_physics_steps == 1


def test_rollout_reuses_physics_capture_hook_for_observation_stages(image_rollout):
    env = image_rollout.env
    del env.sensor_generation
    callbacks, unhooked = [], []

    def hook(callback):
        callbacks.append(callback)
        return lambda: unhooked.append(True)

    env.hook_physics_step = hook
    handlers = env.tool_handlers()
    original_move = handlers["grasp_object"]

    def move(state, args):
        result = original_move(state, args)
        callbacks[0]()
        return result

    env.tool_handlers = lambda: {**handlers, "grasp_object": move}
    result = image_rollout.run("gpt-5.4", [
        [("look", {"camera": "head"}), ("grasp_object", {}), ("look", {"camera": "wrist"})],
        [("done", {"success": False})],
    ])
    assert len(callbacks) == 1 and unhooked == [True]
    assert _image_bytes(result.messages) == image_rollout.saved[1:]
    assert image_rollout.states[-1]._image_physics_steps == 1
    assert image_rollout.states[-1]._image_step_hook_active is False


def test_openeta_receives_fresh_image_each_cycle(image_rollout, monkeypatch, tmp_path):
    from roborsi.agents.baselines import _run_openeta
    from roborsi.embodied.agent_loop import prompt_tools

    requests = []
    replies = iter([("grasp_object", {"object": "cream cheese"}), ("done", {"success": False})])

    def request(model, messages, tools):
        requests.append(copy.deepcopy(messages))
        name, args = next(replies)
        return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            id=f"c{len(requests)}", function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )])

    monkeypatch.setattr(vlm_io, "_call_vlm_tools", request)
    monkeypatch.setattr(history, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(prompt_tools, "_build_tool_specs", lambda **kw: TOOLS)
    result = _run_openeta(
        image_rollout.env, atomic="images", seed=0, tool_budget=4,
        model="gpt-5.4", workspace=SimpleNamespace(root=tmp_path),
        task_instruction="pick cream cheese", ns="libero",
    )
    assert result["tool_calls"] == 4
    assert len(requests) == 2 and len(image_rollout.saved) == 2
    assert image_rollout.saved[0] != image_rollout.saved[1]
    assert _image_bytes(requests[-1]) == image_rollout.saved


@pytest.mark.parametrize("model", ["gpt-5.4", MODELS[1]])
def test_codeact_keeps_each_view_and_preserves_crop_cache(model, image_rollout, monkeypatch, tmp_path):
    from roborsi.embodied import skills
    from roborsi.embodied.sim.robotwin import codeact_runtime

    def step():
        return None

    scene = SimpleNamespace(step=step)
    image_rollout.env._impl = SimpleNamespace(scene=scene)
    monkeypatch.setattr(skills, "discover", lambda: [])
    requests = []

    def request(model, system, messages):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return '```python\nlook(camera="head")\nlook(camera="wrist")\n```'
        assert image_rollout.states[-1].last_image_path.is_file()
        assert image_rollout.states[-1]._pending_images == []
        return '```python\ndone(success=False)\n```'

    monkeypatch.setattr(codeact_runtime, "_vlm_complete_text", request)
    result = codeact_runtime.run_codeact_episode(
        image_rollout.env, seed=0, task_name="images", instruction="observe",
        expected_on_success="observed", model=model, max_turns=2, workdir=tmp_path,
    )
    assert len(_image_bytes(requests[0])) == 1
    assert _image_bytes(requests[1])[1] == image_rollout.saved[1]
    assert len(_image_bytes(requests[1])) == 3  # historical initial, wrist, latest head
    assert "after_turn_00.jpg" in json.dumps(requests[1][-1])
    assert scene.step is step and result.outcome == "vlm_declared_done"


@pytest.mark.parametrize("keep_recent", [1, 2, 3])
def test_compression_keeps_tool_results_and_observations_together(tmp_path, keep_recent):
    path = tmp_path / "view.png"
    Image.new("RGB", (8, 8), "blue").save(path)
    convo = copy.deepcopy(INITIAL)
    for cid in ("old", "recent"):
        convo.extend([
            {"role": "assistant", "content": None, "reasoning_content": f"reason {cid}",
             "tool_calls": [call(cid=cid)]},
            {"role": "tool", "tool_call_id": cid, "content": f"result {cid}"},
        ])
        history._append_image(convo, path)
    original = copy.deepcopy(convo)
    compact = history._summarize_old_trace(convo, keep_recent=keep_recent)
    assert convo == original
    assert compact[3:] == original[-3:]
    assert "result old" in compact[2]["content"]
    assert "reason old" not in compact[2]["content"]
    assert _image_bytes(compact) == [path.read_bytes()]
    history.prepare_chat_messages(compact, require_reasoning=True)


def test_perception_inheritance_is_scoped_and_explicit_override_wins(monkeypatch, wire, tmp_path):
    path = tmp_path / "observation.png"
    Image.new("RGB", (8, 8)).save(path)
    wire.queued.extend([response("image result"), response("package result"), response("explicit")])
    with vlm_io.perception_default(MODELS[0]):
        vlm_io._call_vlm_image("anthropic/claude-sonnet-4-6", "system", "locate", path)
        vlm_io._call_vlm_no_tools("anthropic/claude-sonnet-4-6", INITIAL, request_kind="perception")
        monkeypatch.setenv("ROBORSI_PERCEPTION_MODEL", MODELS[1])
        vlm_io._call_vlm_image(MODELS[0], "system", "locate", path)
    assert [body["model"] for _, body in wire.requests] == [
        "deepseek-flash",
        "deepseek-flash",
        "kimi-k3",
    ]
    assert wire.requests[0][1]["max_tokens"] == 8192
    monkeypatch.delenv("ROBORSI_PERCEPTION_MODEL")
    assert vlm_io.resolve_perception_model() == "anthropic/claude-sonnet-4-6"


def test_execution_model_is_inherited_without_environment_writes(monkeypatch):
    from roborsi.embodied.agent_loop import rollout

    monkeypatch.setenv("ROBORSI_ENGINEER_MODEL", "unrelated-environment-default")
    monkeypatch.setattr(rollout, "DEFAULT_MODEL", "openai/gpt-default")
    requested = []

    def remote_loop(**kwargs):
        requested.append(kwargs["model"])
        assert vlm_io.resolve_perception_model() == (
            os.environ.get("ROBORSI_PERCEPTION_MODEL") or kwargs["model"]
        )
        assert dict(os.environ) == before
        if fail:
            raise RuntimeError("episode failure")
        return "completed"

    env = SimpleNamespace(run_tool_loop=remote_loop)
    for explicit in (None, MODELS[1]):
        if explicit:
            monkeypatch.setenv("ROBORSI_PERCEPTION_MODEL", explicit)
        for model in ("gpt-5.4", "azure/deployment", "anthropic/claude-sonnet-4-6", *MODELS, None):
            for fail in (False, True):
                before = dict(os.environ)
                with vlm_io.perception_default("enclosing-execution"):
                    kwargs = dict(seed=0, task_name="demo", instruction="move",
                                  expected_on_success="held", model=model)
                    if fail:
                        with pytest.raises(RuntimeError, match="episode failure"):
                            rollout.run_rollout(env, **kwargs)
                    else:
                        assert rollout.run_rollout(env, **kwargs) == "completed"
                    assert vlm_io.resolve_perception_model() == (explicit or "enclosing-execution")
                assert dict(os.environ) == before
                assert requested[-1] == (model or "openai/gpt-default")


@pytest.mark.parametrize("backend", ["libero-pro", "robotwin"])
def test_perception_model_reaches_the_actual_tool_dispatch_thread(backend, monkeypatch):
    from roborsi.embodied.agent_loop import rollout

    state = SimpleNamespace(env=SimpleNamespace(backend_name=backend))
    monkeypatch.setattr(rollout, "_dispatch", lambda *args: (vlm_io.resolve_perception_model(), None))
    for model in ("gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS):
        with vlm_io.perception_default(model):
            assert rollout._dispatch_with_timeout(state, {"tool": "look"}) == (model, None)


@pytest.mark.parametrize("model", MODELS)
def test_rollout_keeps_legacy_args_reasoning_and_done_pairing(model, monkeypatch, wire, tmp_path):
    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    dispatched, unhooked = [], []
    env = SimpleNamespace(
        backend_name="libero-pro",
        take_snapshot=lambda: Observation(),
        tool_handlers=lambda: {},
        check_success=lambda: False,
        hook_physics_step=lambda _: lambda: unhooked.append(True),
    )
    monkeypatch.setattr(rollout, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(
        rollout,
        "_dispatch_with_timeout",
        lambda state, action, **kw: (dispatched.append(action) or {"ok": True}, Observation()),
    )
    wire.queued.extend(
        [
            response("I should act", "no-tool-reason"),
            response(calls=[
                call(args='{"x":', cid="bad-json"),
                call(args='{"x":"80"}', cid="coercible"),
                call(args='{}', cid="missing-required"),
            ]),
            response(
                calls=[
                    call(args='{"x":2}', cid="move2"),
                    call("done", '{"success":false}', "done"),
                    call(cid="skipped"),
                ]
            ),
        ]
    )
    with use_run_mode("frozen"):
        result = rollout.run_rollout(
            env,
            seed=0,
            task_name="demo",
            instruction="move",
            expected_on_success="moved",
            model=model,
            tool_budget=3,
            workdir=tmp_path,
            restrict_to_names=set(),
        )
    assert [action["args"] for action in dispatched] == [{}, {"x": "80"}, {}, {"x": 2}]
    assert result.success is False and unhooked == [True]
    assert wire.requests[1][1]["messages"][2]["reasoning_content"] == "no-tool-reason"
    assert result.messages[-1]["tool_call_id"] == "skipped"
    assert json.loads(result.messages[-1]["content"])["executed"] is False
    vlm_io._chat_request(vlm_io.resolve_api_config(model), result.messages, TOOLS)["messages"]
    # The long-horizon caller reuses this history on an atomic retry.
    wire.queued.append(response(calls=[call("done", '{"success":false}', "retry-done")]))
    with use_run_mode("frozen"):
        continued = rollout.run_rollout(
            env,
            seed=0,
            task_name="demo",
            instruction="retry",
            expected_on_success="moved",
            model=model,
            tool_budget=1,
            workdir=tmp_path,
            restrict_to_names=set(),
            prior_messages=result.messages,
        )
    vlm_io._chat_request(vlm_io.resolve_api_config(model), continued.messages, TOOLS)["messages"]
    assert unhooked == [True, True] and len(dispatched) == 4


@pytest.mark.parametrize("model", ["gpt-5.4", "azure/gpt-5.4", "anthropic/claude-sonnet-4-6"])
def test_legacy_rollout_keeps_lax_args_and_completes_done_history(model, monkeypatch, tmp_path):
    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    def reply(calls):
        return SimpleNamespace(content=None, tool_calls=[
            SimpleNamespace(id=c["id"], function=SimpleNamespace(**c["function"]))
            for c in calls
        ])

    replies = iter([
        reply([
            call(args='{"x":', cid="bad-json"),
            call(args='{"x":"80"}', cid="coercible"),
            call(args='{}', cid="missing-required"),
            call("done", '{"success":false}', "done"),
            call(cid="skipped-1"),
            call(args='{"x":', cid="skipped-2"),
        ]),
        reply([call("done", '{"success":false}', "retry-done")]),
    ])
    dispatched, unhooked, sent = [], [], []
    env = SimpleNamespace(
        backend_name="libero-pro",
        take_snapshot=lambda: Observation(),
        tool_handlers=lambda: {},
        check_success=lambda: False,
        hook_physics_step=lambda _: lambda: unhooked.append(True),
    )

    def request(actual_model, messages, tools):
        assert actual_model == model
        sent.append(copy.deepcopy(messages))
        return next(replies)

    monkeypatch.setattr(rollout, "_call_vlm_tools", request)
    monkeypatch.setattr(rollout, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(rollout, "_dispatch_with_timeout", lambda state, action, **kw: (
        dispatched.append(action) or {"ok": True}, Observation(),
    ))
    kwargs = dict(seed=0, task_name="demo", instruction="move", expected_on_success="moved",
                  model=model, tool_budget=1, workdir=tmp_path, restrict_to_names=set())
    with use_run_mode("frozen"):
        result = rollout.run_rollout(env, **kwargs)
        continued = rollout.run_rollout(env, **kwargs, prior_messages=result.messages)

    assert [action["args"] for action in dispatched] == [{}, {"x": "80"}, {}]
    assert result.outcome == "vlm_declared_done" and result.success is False
    assert unhooked == [True, True]
    assert sent[1][:-1] == result.messages
    assert [m["tool_call_id"] for m in result.messages if m["role"] == "tool"] == [
        "bad-json", "coercible", "missing-required", "done", "skipped-1", "skipped-2",
    ]
    for message in result.messages[-2:]:
        assert json.loads(message["content"]) == {
            "ok": False, "executed": False, "skipped": True, "reason": "episode ended by done",
        }
    assert history.prepare_chat_messages(continued.messages)
    expected_ids = [m["tool_call_id"] for m in continued.messages if m["role"] == "tool"]
    if model.startswith("anthropic/"):
        native = history._convert_messages_to_anthropic(continued.messages)
        blocks = [b for m in native if isinstance(m["content"], list) for b in m["content"]]
        assert [b["id"] for b in blocks if b["type"] == "tool_use"] == expected_ids
        assert [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"] == expected_ids
    else:
        _, items = vlm_io._messages_to_responses_input(continued.messages)
        assert [m["call_id"] for m in items if m.get("type") == "function_call"] == expected_ids
        assert [m["call_id"] for m in items if m.get("type") == "function_call_output"] == expected_ids


@pytest.mark.parametrize("model", MODELS)
def test_reviewer_final_request_keeps_tools_but_forbids_execution(
    model, monkeypatch, wire, tmp_path
):
    from roborsi.agents.tools import reviewer

    monkeypatch.setattr(reviewer, "last_frames", lambda _: [])
    monkeypatch.setattr(reviewer, "_MAX_TOOL_TURNS", 1)
    dispatched = []
    monkeypatch.setattr(
        reviewer, "_dispatch", lambda *args: (dispatched.append(args) or {"ok": True}, None)
    )
    wire.queued.extend(
        [
            response(calls=[call("read_document", '{"name":"plan.md"}')]),
            response('{"verdict":"fail"}', "final reason"),
        ]
    )
    result = reviewer.run_review_agent(model, "system", "inspect", tmp_path)
    assert json.loads(result) == {"verdict": "fail"} and len(dispatched) == 1
    body = wire.requests[-1][1]
    assert body["tools"] == reviewer.TOOL_SPECS and body["tool_choice"] == "none"
    assert body["messages"][2]["reasoning_content"] == "original reasoning"


@pytest.mark.parametrize("model", ["gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS])
@pytest.mark.parametrize("encoding,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_reviewer_preserves_tool_defaults_and_passes_definitions_to_adapter(
    model, encoding, mime, monkeypatch, tmp_path
):
    from roborsi.agents.tools import reviewer

    rd = tmp_path / "rollout" / "round_1"
    rd.mkdir(parents=True)
    (rd / "trace.json").write_text(json.dumps([
        {"step": 0, "tool_call": {"tool": "look", "args": {}}, "result": {"ok": True}},
    ]))
    frame = rd / "tick_1.jpg"
    Image.new("RGB", (8, 8)).save(frame, format=encoding)
    monkeypatch.setattr(reviewer, "last_frames", lambda _: [frame])
    monkeypatch.setattr(reviewer, "_MAX_TOOL_TURNS", 1)
    requests = []

    def request(selected, messages, tools, **kwargs):
        requests.append(copy.deepcopy((messages, tools, kwargs)))
        if len(requests) == 2:
            return SimpleNamespace(content='{"verdict":"fail"}')
        raw = response(calls=[
            call("read_steps", '{"round":"1"}', "defaults"),
            call("read_steps", '{"round":', "broken"),
            call("view_frame", '{"round":"1"}', "image-default"),
        ])
        reply = history.normalize_chat_response(SimpleNamespace(
            choices=[SimpleNamespace(**raw["choices"][0])],
        ))
        if not vlm_io.is_compatible_model(model):
            del reply.request_message
        return reply

    monkeypatch.setattr(vlm_io, "_call_vlm_tools", request)
    assert reviewer.run_review_agent(model, "system", "inspect", tmp_path) == '{"verdict":"fail"}'
    messages, tools, kwargs = requests[-1]
    results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    # Missing start/end and string round still reach the original dispatch defaults.
    assert results[0]["ok"] is True and results[0]["steps"][0]["step"] == 0
    assert results[1]["ok"] is False and "round must be" in results[1]["error"]
    assert results[2]["ok"] is True and results[2]["frame"] == frame.name
    expected_image = {"type": "image_url", "image_url": {
        "url": f"data:{mime};base64," + base64.b64encode(frame.read_bytes()).decode(),
    }}
    assert messages[1]["content"][1] == expected_image
    assert messages[-2]["role"] == "user" and messages[-2]["content"][1] == expected_image
    assert tools == reviewer.TOOL_SPECS and kwargs["tool_choice"] == "none"
    history.prepare_chat_messages(messages, require_reasoning=vlm_io.is_compatible_model(model))


@pytest.mark.parametrize("transport", ["chat_completions", "responses"])
def test_openai_final_answer_omits_tools_at_transport_only(transport, monkeypatch):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(output=[], choices=[SimpleNamespace(message=SimpleNamespace(content="done"))])

    client = SimpleNamespace(responses=SimpleNamespace(create=create),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(vlm_io, "_openai_client", lambda: client)
    monkeypatch.setenv("ROBORSI_OPENAI_TRANSPORT", transport)
    for choice in (None, "none"):
        vlm_io._call_vlm_tools("gpt-5.4", INITIAL, TOOLS, tool_choice=choice)
    assert requests[0]["tools"] and requests[0]["tool_choice"] == "auto"
    assert not requests[1].get("tools") and not requests[1].get("tool_choice")


def test_cli_roles_remain_cli(monkeypatch):
    from roborsi.agents.sessions import persistent_agent

    monkeypatch.setenv("ROBORSI_ROLE_SESSION", "1")
    calls = []
    monkeypatch.setattr(
        persistent_agent, "run", lambda *args, **kw: calls.append((args, kw)) or "cli output"
    )
    monkeypatch.setattr(
        vlm_io, "_call_vlm_tools", lambda *args, **kw: pytest.fail("CLI role reached API")
    )
    assert (
        persistent_agent.run_role(
            "planner", "task", "plan", system_prompt="system", model="gpt-5.6-sol"
        )
        == "cli output"
    )
    assert calls[0][1]["model"] == "gpt-5.6-sol"


def test_runtime_fingerprint_does_not_include_secrets(monkeypatch):
    monkeypatch.setenv("ROBORSI_API_AUTH", "not-for-manifest")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://user:password@example.org/v1?token=hidden")
    settings = _api_runtime_settings()
    encoded = json.dumps(settings)
    assert not any(
        word in encoded
        for word in [
            "test-deepseek-key",
            "test-moonshot-key",
            "not-for-manifest",
            "password",
            "hidden",
        ]
    )
    assert settings["settings"]["DEEPSEEK_BASE_URL"] == "https://example.org/v1"
    assert settings["rollout_tool_policy"] == {
        "arguments": "legacy_json", "done_results": "complete",
    }
    assert settings["observation_policy"] == history.OBSERVATION_POLICY


@pytest.mark.parametrize("script", [
    "reproduce_libero_pro.sh", "reproduce_libero_plus.sh", "run_libero_pro_matched_pass1.sh",
])
@pytest.mark.parametrize("model, key", [(MODELS[0], "DEEPSEEK_API_KEY"), (MODELS[1], "MOONSHOT_API_KEY")])
def test_reproduction_script_accepts_mixed_cli_and_api_without_openai_key(script, model, key):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("ROBORSI_", "OPENAI_", "MOONSHOT_", "DEEPSEEK_"))
    }
    env.update(ROBORSI_PLANNER_MODEL="gpt-5.6-sol", ROBORSI_REVIEWER_MODEL="gpt-5.6-sol",
               ROBORSI_ENGINEER_MODEL=model)
    env[key] = "test"
    root = Path(__file__).resolve().parents[1]
    # Exercise the actual startup block without installing dependencies or launching a simulator.
    source = (root / "scripts" / script).read_text()
    config = source.split("# Model configuration:", 1)[1].split("# End model configuration.", 1)[0]
    config = "# Model configuration:" + config
    command = config + '\nprintf "%s\\n" "$ROBORSI_ENGINEER_MODEL" "${ROBORSI_PERCEPTION_MODEL-unset}" "$ROBORSI_REASONING_EFFORT"'
    result = subprocess.run(["bash", "-eu", "-c", command], cwd=root, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [model, "unset", "high"]
    env["ROBORSI_PERCEPTION_MODEL"] = model
    explicit = subprocess.run(["bash", "-eu", "-c", command], cwd=root, env=env,
                              text=True, capture_output=True)
    assert explicit.returncode == 0, explicit.stderr
    assert explicit.stdout.splitlines() == [model, model, "high"]
    env.pop(key)
    missing = subprocess.run(["bash", "-eu", "-c", config], cwd=root, env=env,
                             text=True, capture_output=True)
    assert missing.returncode != 0 and key in missing.stderr


@pytest.mark.parametrize("mode", ["frozen", "evolve"])
def test_real_libero_tool_schemas_are_supported(mode):
    from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
    from roborsi.runtime_mode import use_run_mode

    with use_run_mode(mode):
        tools = _build_tool_specs(ns="libero", task="libero_pick_place")
    assert len(tools) > 10
    assert history.prepare_chat_tools(tools) == tools
    for model in MODELS:
        assert vlm_io._chat_request(vlm_io.resolve_api_config(model), INITIAL, tools)["tools"] == tools


@pytest.mark.parametrize("model", MODELS)
def test_two_compactions_and_continuation_reach_wire(model, wire):
    convo = copy.deepcopy(INITIAL)
    for batch in range(2):
        for i in range(16):
            cid = f"b{batch}-{i}"
            convo.extend(
                [
                    {
                        "role": "assistant",
                        "content": f"old prose {batch}",
                        "reasoning_content": f"old reasoning {batch}",
                        "tool_calls": [call(cid=cid)],
                    },
                    {"role": "tool", "tool_call_id": cid, "content": f"result {batch}-{i}"},
                ]
            )
        convo = history._summarize_old_trace(convo)
        wire.queued.append(response(calls=[call(cid=f"next-{batch}")]))
        msg = vlm_io._call_vlm_tools(model, convo, TOOLS)
        sent = wire.requests[-1][1]["messages"]
        assert "old reasoning" not in sent[2]["content"] and "old prose" not in sent[2]["content"]
        assert "reasoning_content" not in sent[2]
        assert len([m for m in sent if m.get("role") == "assistant"]) == 6
        convo.extend(
            [
                history.serialize_assistant_message(msg),
                {"role": "tool", "tool_call_id": f"next-{batch}", "content": "ok"},
            ]
        )


@pytest.mark.parametrize("mode", ["openeta", "capx"])
def test_baseline_history_keeps_selected_call_or_code_repair(mode, monkeypatch, wire, tmp_path):
    from roborsi.agents import baselines
    from roborsi.embodied.agent_loop import prompt_tools, rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    dispatched = []
    env = SimpleNamespace(
        backend_name="libero-pro",
        tool_handlers=lambda: {},
        check_success=lambda: False,
        reset=lambda _: None,
    )
    monkeypatch.setattr(prompt_tools, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(prompt_tools, "_build_tools_block", lambda **kw: "move(x: int)")
    monkeypatch.setattr(prompt_tools, "_system_prompt", lambda **kw: "Use tools.")
    monkeypatch.setattr(history, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(
        rollout,
        "_dispatch_with_timeout",
        lambda state, action, **kw: (dispatched.append(action) or {"ok": True}, Observation()),
    )
    if mode == "openeta":
        wire.queued.extend(
            [
                response(calls=[call(cid="executed"), call(cid="skipped")]),
                response(calls=[call("done", '{"success":false}')]),
            ]
        )
    else:
        wire.queued.extend(
            [
                response("```python\nassert False\n```", "repair this"),
                response("```python\nmove(x=1)\n```", "repair complete"),
            ]
        )
    with use_run_mode("frozen"):
        result = getattr(baselines, f"_run_{mode}")(
            env,
            atomic="demo",
            seed=0,
            tool_budget=10,
            model=MODELS[1],
            workspace=SimpleNamespace(root=tmp_path),
            task_instruction="move",
            ns="libero",
        )
    assert result["success"] is False
    assert len([a for a in dispatched if a["tool"] == "move"]) == 1
    saved = wire.requests[-1][1]["messages"]
    assert any(m.get("reasoning_content") for m in saved if m["role"] == "assistant")
    if mode == "openeta":
        assistant = next(m for m in saved if m.get("tool_calls"))
        assert [tc["id"] for tc in assistant["tool_calls"]] == ["executed"]
        assert not any(m.get("tool_call_id") == "skipped" for m in saved)
        history.prepare_chat_messages(saved, require_reasoning=True)


@pytest.mark.parametrize("model", ["gpt-5.4", "azure/deployment", "anthropic/claude-sonnet-4-6", *MODELS])
def test_openeta_keeps_only_first_call_with_original_argument_decoding(model, monkeypatch, tmp_path):
    from roborsi.agents import baselines
    from roborsi.embodied.agent_loop import prompt_tools, rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    dispatched, requests = [], []
    env = SimpleNamespace(backend_name="libero-pro", tool_handlers=lambda: {}, check_success=lambda: False)
    monkeypatch.setattr(prompt_tools, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(history, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_dispatch_with_timeout", lambda state, action, **kw:
                        (dispatched.append(action) or {"ok": True}, Observation()))
    batches = iter([
        [call(args='{"x":', cid="first"), call(cid="ignored-1")],
        [call(args='{"x":"80"}', cid="second"), call(cid="ignored-2")],
        [call("done", '{"success":false}', "done"), call(cid="ignored-3")],
    ])

    def request(selected, messages, tools):
        assert selected == vlm_io.resolve_perception_model() == model
        history.prepare_chat_messages(messages, require_reasoning=vlm_io.is_compatible_model(model))
        requests.append(copy.deepcopy(messages))
        raw = response(calls=next(batches))
        reply = history.normalize_chat_response(SimpleNamespace(
            choices=[SimpleNamespace(**raw["choices"][0])],
        ))
        if not vlm_io.is_compatible_model(model):
            del reply.request_message
        return reply

    monkeypatch.setattr(vlm_io, "_call_vlm_tools", request)
    with use_run_mode("frozen"):
        result = baselines._run_openeta(env, atomic="demo", seed=0, tool_budget=8,
                                       model=model, workspace=SimpleNamespace(root=tmp_path),
                                       task_instruction="move", ns="libero")
    assert result["tool_calls"] == 6
    assert [a for a in dispatched if a["tool"] == "move"] == [
        {"tool": "move", "args": {}}, {"tool": "move", "args": {"x": "80"}},
    ]
    saved = requests[-1]
    assert [tc["id"] for m in saved for tc in m.get("tool_calls", [])] == ["first", "second"]
    assert [m["tool_call_id"] for m in saved if m["role"] == "tool"] == ["first", "second"]
    if vlm_io.is_compatible_model(model):
        assert all(m["reasoning_content"] == "original reasoning" for m in saved if m["role"] == "assistant")


def test_codeact_completion_uses_adapter_and_keeps_reasoning(wire):
    from roborsi.embodied.sim.robotwin.codeact_runtime import _vlm_complete_text

    wire.queued.extend([response("bad code", "draft"), response("fixed code", "repair")])
    first = _vlm_complete_text(MODELS[1], "system", INITIAL[1:])
    second = _vlm_complete_text(
        MODELS[1],
        "system",
        INITIAL[1:]
        + [history.serialize_assistant_message(first), {"role": "user", "content": "repair"}],
    )
    assert second == "fixed code"
    assert wire.requests[-1][1]["messages"][2]["reasoning_content"] == "draft"


def test_progress_judge_uses_perception_budget_and_handles_false_string(wire, tmp_path):
    from roborsi.embodied.skills._lib.judging.progress_score import policy

    path = tmp_path / "view.png"
    Image.new("RGB", (8, 8)).save(path)
    wire.queued.extend(
        [
            response('{"done":true,"score":0.9,"reason":"visible"}'),
            response('{"done":"false","score":0.9}'),
        ]
    )
    result = policy.run("pick", str(path), "object held", model=MODELS[1])
    assert result["done"] is True
    assert wire.requests[0][1]["max_completion_tokens"] == 8192
    result = policy.run("pick", str(path), "object held", model=MODELS[1])
    assert result["done"] is False and result["score"] == 0.9


@pytest.mark.parametrize("model", ["gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS])
def test_progress_judge_never_turns_invalid_evidence_into_success(model, monkeypatch, tmp_path):
    import litellm

    from roborsi.embodied.skills._lib.judging.progress_score import policy

    path = tmp_path / "scene.jpg"
    Image.new("RGB", (8, 8)).save(path, format="PNG")
    replies = []

    def completion(*args, **kwargs):
        messages = kwargs.get("messages") or args[1]
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;")
        if vlm_io.is_compatible_model(model):
            assert kwargs["request_kind"] == "perception"
        reply = SimpleNamespace(content=replies.pop(0))
        return reply if args else SimpleNamespace(choices=[SimpleNamespace(message=reply)])

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(vlm_io, "_call_vlm_tools", completion)
    for raw_done, expected_done in [(False, False), (" false ", False), ("0", False),
                                     (0, False), (True, True), ("TRUE", True), (1, True)]:
        reason = 'visible } is text, not the end of JSON; quoted "brace"'
        replies.append("```json\n" + json.dumps({"done": raw_done, "score": "0.25", "reason": reason}) + "\n```")
        result = policy.run("pick", str(path), "object held", model=model)
        assert result["done"] is expected_done and result["score"] == 0.25
        assert result["reason"] == reason
    for payload in [
        {"done": "maybe", "score": 0.5}, {"done": [], "score": 0.5},
        {"done": None, "score": 0.5}, {"done": 2, "score": 0.5},
        *({"done": True, "score": score} for score in
          ("NaN", "Infinity", "1e999", -0.1, 1.1, True, None, [])),
    ]:
        replies.append(json.dumps(payload))
        with pytest.raises(vlm_io.ProviderError) as exc:
            policy.run("pick", str(path), "object held", model=model)
        assert exc.value.category == "response"
    replies.append("not JSON")
    with pytest.raises(vlm_io.ProviderError, match="JSON object"):
        policy.run("pick", str(path), "object held", model=model)


def test_reviewer_indices_do_not_select_the_wrong_round_or_abort_review(tmp_path):
    from roborsi.agents.tools import reviewer

    for index in (1, 2):
        rd = tmp_path / "rollout" / f"round_{index:02d}"
        rd.mkdir(parents=True)
        (rd / "trace.json").write_text(json.dumps([{"step": index}]))
        Image.new("RGB", (8, 8)).save(rd / "tick_1.jpg")
    for args in (None, [], {}, {"round": 0}, {"round": -1}, {"round": 3},
                 {"round": "bad"}, {"round": float("inf")},
                 {"round": 1, "start": "bad"}, {"round": 1, "end": None}):
        result, image = reviewer._dispatch(tmp_path, "read_steps", args)
        assert result["ok"] is False and image is None
    result, _ = reviewer._dispatch(tmp_path, "read_steps", {"round": "1"})
    assert result["steps"] == [{"step": 1}]
    result, _ = reviewer._dispatch(tmp_path, "read_steps", {"round": 1, "end": -1})
    assert result["ok"] is True and result["steps"] == []
    result, image = reviewer._dispatch(tmp_path, "view_frame", {"round": "2", "index": "-1"})
    assert result["ok"] is True and image.parent.name == "round_02"
    result, image = reviewer._dispatch(tmp_path, "view_frame", {"round": 2, "index": "bad"})
    assert result["ok"] is False and image is None


@pytest.mark.parametrize("model", ["gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS])
@pytest.mark.parametrize("scenario", ["complete", "missing", "wrong_seeds", "process_error", "failure"])
def test_gate_uses_api_model_in_child_and_does_not_score_missing_episodes(
    model, scenario, monkeypatch, tmp_path
):
    from roborsi.agents.evolution import episode_gate as gate

    monkeypatch.setenv("ROBORSI_GATE_MODEL", model)
    monkeypatch.setenv("ROBORSI_ENGINEER_MODEL", MODELS[0])
    monkeypatch.setenv("ROBORSI_ROLE_SESSION", "1")
    monkeypatch.setattr(gate, "DEV_SEEDS", [21, 22])
    monkeypatch.setattr(gate, "MIN_PASS", 2)
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **kw: str(tmp_path / "gate"))
    children = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "worktree", "add"]:
            transport = Path(cmd[4]) / "roborsi/embodied/agent_loop/vlm_io.py"
            transport.parent.mkdir(parents=True)
            transport.write_text("def _chat_call_with_tools(): pass\n")
        elif "eval-suite" in cmd:
            children.append((cmd, kwargs))
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True)
            seeds = ([21] if scenario == "missing" else [31, 32]
                     if scenario == "wrong_seeds" else gate.DEV_SEEDS)
            (out / "episodes.jsonl").write_text(
                "\n".join(json.dumps({"seed": s, "verdict": "failure" if scenario == "failure" else "success"})
                          for s in seeds)
            )
            return SimpleNamespace(returncode=1 if scenario == "process_error" else 0)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    report = gate.run({}, "libero_object_task/0", apply_candidate=False)
    cmd, kwargs = children[0]
    assert all(
        cmd[cmd.index(f"--{role}-model") + 1] == model
        for role in ("planner", "engineer", "reviewer")
    )
    assert kwargs["env"]["ROBORSI_ROLE_SESSION"] == "0"
    assert kwargs["env"]["MOONSHOT_API_KEY"] == "test-moonshot-key"
    assert os.environ["ROBORSI_ROLE_SESSION"] == "1"
    assert report.overall_pass is (scenario == "complete")
    assert report.details["complete"] is (scenario in {"complete", "failure"})
    assert report.details["passes"] == ({"complete": 2, "failure": 0}.get(scenario))
    assert "test-moonshot-key" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("seeds,need", [([], 0), ([21, 23], 1), ([21, 21], 1),
                                        ([21, 22], -1), ([21, 22], 3)])
def test_gate_rejects_unrepresentable_seeds_and_invalid_threshold_before_launch(seeds, need, monkeypatch):
    from roborsi.agents.evolution import episode_gate as gate

    monkeypatch.setattr(gate, "DEV_SEEDS", seeds)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: pytest.fail("invalid gate launched"))
    with pytest.raises(vlm_io.ProviderError) as exc:
        gate.run({}, "libero_object_task/0", min_pass=need)
    assert exc.value.category == "configuration"


@pytest.mark.parametrize("cand_passes,base_passes,cand_complete,base_complete,expected", [
    (0, 0, True, True, True), (2, 1, True, True, True), (0, 1, True, True, False),
    (None, None, False, False, False), (2, 0, True, False, False), (2, 1, False, True, False),
])
def test_cross_task_gate_requires_two_valid_measurements(
    cand_passes, base_passes, cand_complete, base_complete, expected, monkeypatch
):
    from roborsi.agents.evolution import episode_gate as gate

    monkeypatch.setenv("ROBORSI_GLOBAL_GATE_TASKS", "1")
    monkeypatch.setattr(gate, "tasks_using_skill", lambda *args: ["other-task"])
    reports = iter([
        gate.EpisodeGateReport(cand_complete, "candidate", {"passes": cand_passes}),
        gate.EpisodeGateReport(base_complete, "baseline", {"passes": base_passes}),
    ])
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return next(reports)

    monkeypatch.setattr(gate, "run", run)
    report = gate.cross_task_gate({"name": "move"}, "source-task")
    assert report.overall_pass is expected
    assert calls == [{"min_pass": 0}, {"apply_candidate": False, "min_pass": 0}]
    if not cand_complete or not base_complete:
        assert "incomplete measurement" in report.note


def test_gate_rejects_uncommitted_adapter_before_model_call(monkeypatch, tmp_path):
    from roborsi.agents.evolution import episode_gate as gate

    monkeypatch.setenv("ROBORSI_GATE_MODEL", MODELS[0])
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **kw: str(tmp_path / "gate"))
    cmds = []
    monkeypatch.setattr(
        gate.subprocess, "run", lambda cmd, **kw: cmds.append(cmd) or SimpleNamespace(returncode=0)
    )
    with pytest.raises(vlm_io.ProviderError, match="committed HEAD"):
        gate.run({}, "libero_object_task/0", apply_candidate=False)
    assert all("eval-suite" not in cmd for cmd in cmds)


def test_api_errors_are_not_negative_visual_observations(monkeypatch):
    import numpy as np

    from roborsi.embodied.skills.base._lib.libero import _perception

    state = SimpleNamespace(
        env=SimpleNamespace(
            take_snapshot=lambda: SimpleNamespace(
                images={"head_camera": np.zeros((8, 8, 3), dtype=np.uint8)}
            )
        )
    )
    monkeypatch.setattr(_perception, "_requires_orbit_product_identity", lambda _: False)
    monkeypatch.setattr(_perception, "local_vlm_point", lambda *args: None)

    def fail(*args):
        raise vlm_io.ProviderError("bad credentials", category="configuration")

    monkeypatch.setattr(_perception, "vlm_point", fail)
    with pytest.raises(vlm_io.ProviderError, match="bad credentials"):
        _perception.localize_precise(state, "block")


def test_legacy_claude_still_uses_native_stream(monkeypatch):
    from contextlib import contextmanager

    captured = []
    native_reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="legacy reply")])

    @contextmanager
    def stream(**kwargs):
        captured.append(kwargs)
        yield SimpleNamespace(get_final_message=lambda: native_reply)

    monkeypatch.setattr(
        vlm_io,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(stream=stream)),
    )
    monkeypatch.setattr(
        vlm_io, "_chat_call_with_tools", lambda *a, **kw: pytest.fail("Claude entered compatible Chat")
    )
    msg = vlm_io._call_vlm_tools("anthropic/claude-sonnet-4-6", INITIAL, [], thinking_budget=4000)
    assert msg.content == "legacy reply"
    assert captured[0]["thinking"] == {"type": "adaptive"}
    assert captured[0]["max_tokens"] == 68000
    assert "reasoning_effort" not in captured[0]


@pytest.mark.parametrize("args,decoded", [('{"x":"invalid"}', {"x": "invalid"}), ('{"x":', {})])
def test_outer_chat_retains_reasoning_and_delegates_lax_args(args, decoded, monkeypatch, wire):
    from roborsi.channels.core import agent
    from roborsi.embodied.agent_loop import config
    from roborsi.runtime_mode import use_run_mode

    monkeypatch.setenv("ROBORSI_3ROLE", "0")
    monkeypatch.setattr(config, "DEFAULT_MODEL", MODELS[1])
    monkeypatch.setattr(agent, "_outer_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(agent, "_get_skill_index", lambda: "move")
    executed = []
    monkeypatch.setattr(agent, "_exec_tool", lambda name, args, *a, **kw:
                        executed.append((name, args)) or '{"ok":false,"error":"tool result"}')
    wire.queued.extend(
        [response(calls=[call(args=args)]), response("finished", "end")]
    )
    convo = []
    with use_run_mode("frozen"):
        text = agent.handle_user_message("inspect", history=convo, max_hops=2)
    assert text == "finished"
    vlm_io._chat_request(vlm_io.resolve_api_config(MODELS[1]), convo, TOOLS)["messages"]
    assert convo[-1]["reasoning_content"] == "end"
    assert executed == [("move", decoded)]
    assert json.loads(convo[-2]["content"]) == {"ok": False, "error": "tool result"}


def test_rollout_api_failure_unhooks_without_robot_action(monkeypatch, wire, tmp_path):
    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    unhooked = []
    env = SimpleNamespace(
        backend_name="libero-pro",
        take_snapshot=lambda: Observation(),
        tool_handlers=lambda: {},
        hook_physics_step=lambda _: lambda: unhooked.append(True),
    )
    monkeypatch.setattr(rollout, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(
        rollout, "_dispatch_with_timeout", lambda *a, **kw: pytest.fail("action after API failure")
    )
    wire.queued.append(401)
    with use_run_mode("frozen"), pytest.raises(vlm_io.ProviderError):
        rollout.run_rollout(
            env,
            seed=0,
            task_name="demo",
            instruction="move",
            expected_on_success="moved",
            model=MODELS[0],
            tool_budget=1,
            workdir=tmp_path,
            restrict_to_names=set(),
        )
    assert unhooked == [True]


@pytest.mark.parametrize("model", ["gpt-5.4", "anthropic/claude-sonnet-4-6", *MODELS])
@pytest.mark.parametrize("stage", ["setup", "model", "tool", "image", "interrupt"])
def test_rollout_restores_physics_hook_on_every_exception(model, stage, monkeypatch, tmp_path):
    from roborsi.embodied.agent_loop import rollout
    from roborsi.embodied.agent_loop.env import Observation
    from roborsi.runtime_mode import use_run_mode

    previous_callback = object()
    installed = {"callback": previous_callback}
    restored = []

    def hook(callback):
        installed["callback"] = callback

        def unhook():
            restored.append(True)
            installed["callback"] = previous_callback

        return unhook

    env = SimpleNamespace(backend_name="libero-pro", take_snapshot=lambda: Observation(),
                          tool_handlers=lambda: {}, hook_physics_step=hook,
                          check_success=lambda: pytest.fail("adjudication after exception"))
    error = KeyboardInterrupt("stop") if stage == "interrupt" else RuntimeError("original failure")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(rollout, "_initial_messages", lambda *a, **kw: copy.deepcopy(INITIAL))
    monkeypatch.setattr(rollout, "_build_tool_specs", lambda **kw: TOOLS)
    monkeypatch.setattr(rollout, "_call_vlm_tools", lambda *a, **kw: SimpleNamespace(
        content=None, tool_calls=[SimpleNamespace(id="move", function=SimpleNamespace(
            name="move", arguments='{"x":1}',
        ))],
    ))
    monkeypatch.setattr(rollout, "_dispatch_with_timeout", lambda *a, **kw: ({"ok": True}, Observation()))
    target = {"setup": "_initial_messages", "model": "_call_vlm_tools", "tool": "_dispatch_with_timeout",
              "image": "_append_pending_images", "interrupt": "_call_vlm_tools"}[stage]
    monkeypatch.setattr(rollout, target, fail)
    with use_run_mode("frozen"), pytest.raises(type(error)) as exc:
        rollout.run_rollout(env, seed=0, task_name="demo", instruction="move", expected_on_success="held",
                            model=model, tool_budget=1, workdir=tmp_path, restrict_to_names=set())
    assert exc.value is error
    assert restored == [True] and installed["callback"] is previous_callback


def test_capacity_checks_stop_requests_before_network(monkeypatch, wire, tmp_path):
    from dataclasses import replace

    cfg = vlm_io.resolve_api_config(MODELS[0])
    tiny = replace(cfg, profile=replace(cfg.profile, max_body_bytes=10))
    with pytest.raises(vlm_io.ProviderError, match="body-size"):
        vlm_io._chat_request(tiny, INITIAL, TOOLS)
    tiny = replace(cfg, profile=replace(cfg.profile, context_length=10))
    with pytest.raises(vlm_io.ProviderError, match="context"):
        vlm_io._chat_request(tiny, INITIAL, TOOLS)
    path = tmp_path / "oversized.png"
    Image.new("RGB", (8193, 1)).save(path)
    with pytest.raises(vlm_io.ProviderError, match="dimension"):
        vlm_io._chat_request(cfg, [{"role": "user", "content": [history.image_block(path)]}], [])
    monkeypatch.setenv("ROBORSI_API_MAX_OUTPUT_TOKENS", "999999999")
    with pytest.raises(vlm_io.ProviderError, match="Output budget"):
        vlm_io._call_vlm_tools(MODELS[0], INITIAL, TOOLS)
    assert not wire.requests


@pytest.mark.parametrize("changed_setting", ["output_budget", "rollout_tool_policy", "observation_policy"])
def test_evolve_resume_rejects_api_configuration_drift(tmp_path, changed_setting):
    from datetime import datetime, timezone

    from roborsi.evaluation.suite import _load_or_create_campaign

    kwargs = dict(
        path=tmp_path / "campaign.json",
        generated_id="test",
        backend="libero-pro",
        atomic="libero_pick_place",
        task_keys=["libero_object_task/0"],
        seeds=1,
        seed_start=0,
        workers=1,
        tool_budget=2,
        infra_retries=0,
        run_mode="evolve",
        planner_model="gpt-5.6-sol",
        engineer_model=MODELS[0],
        reviewer_model="gpt-5.6-sol",
        reasoning_effort="high",
        atomic_compound_enabled=True,
        agent_mode="roborsi",
        journal=tmp_path / "episodes.jsonl",
        created_at=datetime.now(timezone.utc),
        runtime={"roborsi_commit": "before", "api_adapter": _api_runtime_settings()},
    )
    _load_or_create_campaign(**kwargs)
    # Skill commits may change during evolution, but API settings may not.
    kwargs["runtime"] = copy.deepcopy(kwargs["runtime"])
    kwargs["runtime"]["roborsi_commit"] = "after"
    _load_or_create_campaign(**kwargs)
    if changed_setting == "output_budget":
        kwargs["runtime"]["api_adapter"]["settings"]["ROBORSI_API_MAX_OUTPUT_TOKENS"] = "32768"
    elif changed_setting == "observation_policy":
        kwargs["runtime"]["api_adapter"]["observation_policy"] = "legacy_last_image_path"
    else:
        kwargs["runtime"]["api_adapter"]["rollout_tool_policy"]["arguments"] = "strict_schema"
    with pytest.raises(ValueError, match="api_adapter"):
        _load_or_create_campaign(**kwargs)


