"""Board hub for sim: live_trace.emit_inner must flow through the process-wide
app_board → sim bridge → back into the per-chat live_trace session + trace.db,
reproducing the projection a direct ``session.append`` produced before.
"""
from __future__ import annotations

from roborsi.channels.agent.feishu import live_trace
from roborsi.store import trace_db


def test_emit_inner_routes_through_board_to_session_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))
    # Force schema creation against this test's fresh DB (init() is cached).
    monkeypatch.setattr(trace_db, "_INITIALISED", False)

    live_trace.set_inner_target(live_trace.get_session("c1"))
    live_trace.set_inner_run_id("r1")

    live_trace.emit_inner("inner_tool_call", step=0, tool="look",
                          args={}, reasoning="r")

    # (a) Event came back into session c1 via the bridge (not dropped).
    sess = live_trace.get_session("c1")
    call = next((e for e in sess.events if e["kind"] == "inner_tool_call"), None)
    assert call is not None, [e["kind"] for e in sess.events]
    assert call["tool"] == "look" and call["run_id"] == "r1"

    # (b) Step projection landed in trace.db (the /run page + bench source).
    steps = trace_db.list_steps(chat_id="c1")
    call_row = next((s for s in steps if s["tool"] == "look"), None)
    assert call_row is not None, steps
    assert call_row["run_id"] == "r1"
    assert call_row["chat_id"] == "c1"
    assert call_row["layer"] == "inner"
    assert call_row["idx"] == 0

    # (c) A result event projects result_ok.
    live_trace.emit_inner("inner_tool_result", step=0, tool="look",
                          ok=True, preview="x")
    res = next((e for e in sess.events if e["kind"] == "inner_tool_result"), None)
    assert res is not None
    result_row = next(
        (s for s in trace_db.list_steps(chat_id="c1")
         if s["tool"] == "look" and s["result_ok"] is not None), None)
    assert result_row is not None
    assert result_row["result_ok"] == 1
    assert result_row["result_preview"] == "x"


def test_emit_inner_no_target_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace2.db"))
    monkeypatch.setattr(trace_db, "_INITIALISED", False)

    live_trace.set_inner_target(None)
    live_trace.emit_inner("inner_tool_call", step=0, tool="look", args={})

    # Nothing attributed → nothing published → no step row written anywhere.
    assert trace_db.list_steps() == []
