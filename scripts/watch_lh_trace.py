#!/usr/bin/env python3
"""Live tail of LH 3-role inner trace.

Polls trace.db every N seconds, prints new inner_tool_call / inner_tool_result
events for the most recent lh3role chat_id. Use this to watch what the
Engineer is doing in real time without grepping log files.

Usage:
    python3 scripts/watch_lh_trace.py             # auto-detect newest LH chat
    python3 scripts/watch_lh_trace.py <chat_id>   # specific chat
    python3 scripts/watch_lh_trace.py --interval 5
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


DB = Path.home() / ".roborsi" / "trace.db"


def newest_lh_chat() -> str | None:
    if not DB.exists():
        return None
    db = sqlite3.connect(str(DB))
    cur = db.execute(
        "SELECT chat_id FROM events "
        "WHERE chat_id LIKE 'lh3role-%' "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else None


def fetch_new_steps(chat_id: str, last_id: int) -> list[tuple]:
    db = sqlite3.connect(str(DB))
    return db.execute(
        "SELECT id, ts, run_id, tool, args_json, result_ok, result_preview "
        "FROM steps WHERE chat_id=? AND id>? AND layer='inner' "
        "ORDER BY id ASC",
        (chat_id, last_id)
    ).fetchall()


def fetch_new_events(chat_id: str, last_id: int) -> list[tuple]:
    db = sqlite3.connect(str(DB))
    return db.execute(
        "SELECT id, ts, kind, payload_json FROM events "
        "WHERE chat_id=? AND id>? "
        "AND kind IN ('lh_attempt_start','lh3role_planned','lh3role_executed',"
        "             'lh3role_reviewed','lh3role_exception','done') "
        "ORDER BY id ASC",
        (chat_id, last_id)
    ).fetchall()


def fmt_step(row: tuple) -> str:
    _id, ts, run_id, tool, args, ok, preview = row
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
    args_short = (args or "")[:60].replace("\n", " ")
    ok_marker = "✓" if ok == 1 else ("✗" if ok == 0 else " ")
    short_run = (run_id or "?").split("-")[-1] if run_id else "?"
    line = f"  {ts_str} [{short_run:>8}] {ok_marker} {tool}"
    if args_short:
        line += f"  {args_short}"
    if ok is not None and preview:
        line += f"\n             → {(preview or '')[:120]}"
    return line


def fmt_event(row: tuple) -> str:
    _id, ts, kind, payload = row
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
    return f"  {ts_str} ── {kind}  {(payload or '')[:160]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("chat_id", nargs="?")
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args()

    chat = args.chat_id or newest_lh_chat()
    if not chat:
        print("no lh3role-* chat found in trace.db", file=sys.stderr)
        return 1
    print(f"watching chat_id={chat}  (poll {args.interval}s)\n")
    last_step = 0
    last_event = 0
    # Replay tail of existing first
    for row in fetch_new_steps(chat, 0)[-10:]:
        print(fmt_step(row))
        last_step = max(last_step, row[0])
    for row in fetch_new_events(chat, 0)[-5:]:
        print(fmt_event(row))
        last_event = max(last_event, row[0])
    print("\n--- live tail ---")
    while True:
        try:
            time.sleep(args.interval)
            for row in fetch_new_events(chat, last_event):
                print(fmt_event(row))
                last_event = row[0]
            for row in fetch_new_steps(chat, last_step):
                print(fmt_step(row))
                last_step = row[0]
        except KeyboardInterrupt:
            print("\nexit")
            return 0


if __name__ == "__main__":
    sys.exit(main())
