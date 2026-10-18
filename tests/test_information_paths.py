"""Regression tests for information that used to be lost between the
simulator, the roles and the model."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from roborsi.embodied.agent_loop.messages import _attach_latest_image, _summarize_old_trace


def _images(convo):
    return sum(1 for m in convo if isinstance(m.get("content"), list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "image_url")


def test_a_new_frame_in_the_same_file_is_attached_again(tmp_path):
    frame = tmp_path / "look_head_camera.jpg"
    frame.write_bytes(b"first")
    state = SimpleNamespace(last_image_path=frame, _attached_image_path=None)
    convo = [{"role": "user", "content": "go"}]
    convo = _attach_latest_image(convo, state)
    assert _images(convo) == 1
    convo.append({"role": "tool", "tool_call_id": "c1", "content": "{}"})
    convo = _attach_latest_image(convo, state)          # nothing new
    assert _images(convo) == 1
    frame.write_bytes(b"first")                          # same frame rewritten
    convo = _attach_latest_image(convo, state)
    assert _images(convo) == 1
    frame.write_bytes(b"second")                         # look() again: same path
    convo = _attach_latest_image(convo, state)
    assert _images(convo) == 2


def test_compaction_keeps_earlier_summaries_and_tool_texts():
    convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i in range(20):
        convo.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "function": {"name": f"tool{i}", "arguments": "{}"}}]})
        convo.append({"role": "tool", "tool_call_id": f"c{i}",
                      "content": [{"type": "text", "text": f"result{i}"},
                                  {"type": "image_url", "image_url": {"url": "x"}}]})
    once = _summarize_old_trace(convo, keep_recent=12)
    summary = next(m["content"] for m in once if str(m.get("content", "")).startswith("[TRACE SUMMARY"))
    assert "tool0(" in summary and "result0" in summary
    for i in range(20, 30):
        once.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "function": {"name": f"tool{i}", "arguments": "{}"}}]})
        once.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"result{i}"})
    twice = _summarize_old_trace(once, keep_recent=12)
    summary = next(m["content"] for m in twice if str(m.get("content", "")).startswith("[TRACE SUMMARY"))
    assert "tool0(" in summary and "tool20(" in summary


def test_planner_markdown_keeps_inner_code_blocks():
    from roborsi.agents.roles.planner import _extract_json_and_md
    reply = ('```json\n{"goal": "g"}\n```\n```markdown\n# Plan\n1. exec_python with:\n'
             '```python\nprint(1)\n```\n## Success criteria\nbowl on plate\n```\n')
    spec, md = _extract_json_and_md(reply)
    assert spec == {"goal": "g"}
    assert "print(1)" in md and "## Success criteria" in md and md.endswith("bowl on plate")


def test_review_json_survives_braces_in_surrounding_text():
    from roborsi.agents.roles.reviewer import _parse_review_json
    raw = 'Scene: bowl at {left}.\n{"verdict": "continue", "root_cause": "x {y}"}\nthanks {z}'
    assert _parse_review_json(raw)["verdict"] == "continue"
    assert _parse_review_json("no json here") == {}


def test_incomplete_responses_are_reported():
    from roborsi.embodied.agent_loop.vlm_io import _wrap_openai_responses
    resp = SimpleNamespace(output=[], status="incomplete",
                           incomplete_details=SimpleNamespace(reason="max_output_tokens"))
    msg = _wrap_openai_responses(resp)
    assert msg.tool_calls is None and "max_output_tokens" in msg.content


def test_engineer_wiki_keeps_successes_when_trimmed():
    from roborsi.agents.roles.engineer import fit_wiki
    md = ("# wiki\n## Successful sequences\n" + "SUCCESS-SEQ\n" * 50
          + "## Failure traces\n" + "fail line\n" * 3000 + "## Manager-approved leads\n- lead A\n")
    out = fit_wiki(md, 4000)
    assert len(out) <= 4000
    assert "SUCCESS-SEQ" in out and "lead A" in out


def test_codex_stdin_marker_is_not_read_as_an_image():
    from roborsi.agents.sessions._codex_autoloop.codex_runner import CodexRunner, RunnerOptions
    runner = CodexRunner("codex")
    cmd = runner._build_codex_command(resume_thread_id=None,
                                      options=RunnerOptions(images=["a.jpg"]))
    assert cmd[-2:] == ["--", "-"]


def test_gate_puts_the_candidate_where_the_task_loads_it(tmp_path):
    from roborsi.agents.evolution.episode_gate import _write_candidate
    local = tmp_path / "roborsi/embodied/skills/task_local/libero_goal__5/base/push_object/libero"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("old")
    _write_candidate(tmp_path, {"name": "push_object", "new_code": "NEW", "skill_md": "md"}, "libero_goal/5")
    assert (local / "policy.py").read_text() == "NEW"
    assert not (tmp_path / "roborsi/embodied/skills/base/push_object/libero/policy.py").exists()


def test_reviewer_sees_the_starting_scene_and_the_latest_views(tmp_path):
    from roborsi.agents.tools.reviewer import review_frames
    rollout = tmp_path / "rollout"
    (rollout / "round_01").mkdir(parents=True)
    (rollout / "initial_view_head_camera.jpg").write_bytes(b"s")
    (rollout / "initial_view_wrist.jpg").write_bytes(b"w")
    (rollout / "round_01" / "final_view_head_camera.jpg").write_bytes(b"f")
    names = [p.name for p in review_frames(tmp_path)]
    assert names == ["initial_view_head_camera.jpg", "final_view_head_camera.jpg"]
