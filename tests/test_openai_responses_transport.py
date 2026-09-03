from __future__ import annotations

from types import SimpleNamespace

from roborsi.embodied.agent_loop import vlm_io


def test_messages_to_responses_input_preserves_tools_and_images() -> None:
    instructions, items = vlm_io._messages_to_responses_input([
        {"role": "system", "content": "Use visible evidence only."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect the scene."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,abc"},
                },
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "look", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "{\"ok\": true}",
        },
    ])

    assert instructions == "Use visible evidence only."
    assert items[0]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/jpeg;base64,abc",
    }
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "look",
        "arguments": "{}",
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "{\"ok\": true}",
    }


def test_wrap_openai_responses_matches_existing_tool_interface() -> None:
    response = SimpleNamespace(output=[
        SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text="I will inspect.")],
        ),
        SimpleNamespace(
            type="function_call",
            id="fc_1",
            call_id="call_1",
            name="look",
            arguments="{\"camera\":\"head\"}",
        ),
    ])

    message = vlm_io._wrap_openai_responses(response)

    assert message.content == "I will inspect."
    assert message.tool_calls[0].id == "call_1"
    assert message.tool_calls[0].function.name == "look"
    assert message.tool_calls[0].function.arguments == "{\"camera\":\"head\"}"


def test_openai_responses_call_uses_flat_function_schema(monkeypatch) -> None:
    captured = {}
    response = SimpleNamespace(
        output=[],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
    )

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response

    client = SimpleNamespace(responses=Responses())
    monkeypatch.setenv("ROBORSI_OPENAI_MAX_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("ROBORSI_REASONING_EFFORT", "medium")

    with vlm_io.capture_usage() as usage:
        message = vlm_io._openai_responses_call(
            client,
            model_id="gpt-test",
            messages=[
                {"role": "system", "content": "Plan carefully."},
                {"role": "user", "content": "Look."},
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "look",
                    "description": "Capture a frame.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )

    assert message.content is None
    assert captured["model"] == "gpt-test"
    assert captured["instructions"] == "Plan carefully."
    assert captured["max_output_tokens"] == 256
    assert captured["reasoning"] == {"effort": "medium"}
    assert captured["tools"] == [{
        "type": "function",
        "name": "look",
        "description": "Capture a frame.",
        "parameters": {"type": "object", "properties": {}},
    }]
    assert captured["tool_choice"] == "auto"
    assert usage.vlm_calls == 1
    assert usage.metered_calls == 1
    assert usage.unmetered_calls == 0
