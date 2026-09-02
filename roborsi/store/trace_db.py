"""SQLite trace store for roborsi.

Single DB file at ``~/.roborsi/trace.db`` (override with env
``ROBORSI_TRACE_DB``). WAL mode + per-call connections — safe for
concurrent reads + writes from multiple threads in one process.

Tables:
  runs           — one row per ``run_task_sync`` invocation.
  steps          — outer (chat) and inner (atomic) agent tool calls/results.
  proposals      — skill_update / new_skill proposals queued by the agent.
  benches        — periodic benchmark scores per skill × model.
  vla_episodes   — pointers into the on-disk asset store, filtered for VLA
                   training dataset export.

All write functions are safe to call from any thread. The schema is created
lazily on first import (``init()``). Reads return plain dicts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    task            TEXT NOT NULL,
    skill           TEXT,
    status          TEXT,
    outcome         TEXT,
    model           TEXT,
    seed            INTEGER,
    chat_id         TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    summary         TEXT,
    video_path      TEXT,
    wallclock_s     REAL,
    tokens_used     INTEGER,
    log_tail        TEXT,
    episode_summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_skill   ON runs(skill);
CREATE INDEX IF NOT EXISTS idx_runs_chat    ON runs(chat_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);

CREATE TABLE IF NOT EXISTS steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    chat_id         TEXT,
    layer           TEXT,          -- 'outer' (chat-level) or 'inner' (atomic)
    idx             INTEGER,
    tool            TEXT,
    args_json       TEXT,
    result_ok       INTEGER,       -- 0/1/NULL
    result_preview  TEXT,
    reasoning       TEXT,
    ts              REAL           -- unix epoch
);
CREATE INDEX IF NOT EXISTS idx_steps_run  ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_steps_chat ON steps(chat_id, ts);

CREATE TABLE IF NOT EXISTS proposals (
    id              TEXT PRIMARY KEY,
    run_id          TEXT,
    skill           TEXT,
    kind            TEXT,          -- 'update' or 'new'
    file_path       TEXT,
    diff            TEXT,
    rationale       TEXT,
    status          TEXT,          -- pending | applied | rejected | reverted
    created_at      TEXT,
    applied_at      TEXT,
    applied_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_skill  ON proposals(skill);

CREATE TABLE IF NOT EXISTS benches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill           TEXT,
    model           TEXT,
    seeds_passed    INTEGER,
    seeds_total     INTEGER,
    avg_tool_calls  REAL,
    run_at          TEXT,
    commit_sha      TEXT,
    tag             TEXT
);
CREATE INDEX IF NOT EXISTS idx_benches_skill ON benches(skill, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_benches_tag   ON benches(tag);

CREATE TABLE IF NOT EXISTS vla_episodes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT,
    skill               TEXT,
    success             INTEGER,
    frames_dir          TEXT,
    action_jsonl        TEXT,
    language            TEXT,
    used_in_training    INTEGER DEFAULT 0,
    created_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_vla_skill ON vla_episodes(skill, success);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT,
    kind            TEXT,
    payload_json    TEXT,
    ts              REAL
);
CREATE INDEX IF NOT EXISTS idx_events_chat ON events(chat_id, id);
"""


def db_path() -> Path:
    p = Path(os.environ.get(
        "ROBORSI_TRACE_DB",
        str(Path.home() / ".roborsi" / "trace.db")))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path()), check_same_thread=False,
                         isolation_level=None, timeout=10.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory = sqlite3.Row
    return c


_INITIALISED = False


def init() -> None:
    global _INITIALISED
    if _INITIALISED:
        return
    with _LOCK:
        if _INITIALISED:
            return
        c = _conn()
        try:
            # Migrations first — add columns that older DBs may lack so
            # the indexes in SCHEMA can reference them.
            _migrate_benches_add_tag(c)
            c.executescript(SCHEMA)
        finally:
            c.close()
        _INITIALISED = True


def _migrate_benches_add_tag(c: sqlite3.Connection) -> None:
    """Idempotently add benches.tag for DBs created before it existed."""
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='benches'"
    ).fetchone()
    if not row:
        return                                      # SCHEMA will create it
    cols = {r["name"] for r in c.execute("PRAGMA table_info(benches)").fetchall()}
    if "tag" not in cols:
        c.execute("ALTER TABLE benches ADD COLUMN tag TEXT")


# ── writes ────────────────────────────────────────────────────────────────

def insert_run(run_id: str, task: str, skill: str | None = None,
                model: str | None = None, seed: int | None = None,
                chat_id: str | None = None) -> None:
    init()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT OR REPLACE INTO runs "
                "(id, task, skill, model, seed, chat_id, status, started_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run_id, task, skill, model, seed, chat_id, "running",
                 time.strftime("%Y-%m-%d %H:%M:%S")))
        finally:
            c.close()


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    init()
    # episode_summary is a dict — serialise to json column.
    if "episode_summary" in fields:
        fields["episode_summary_json"] = json.dumps(
            fields.pop("episode_summary"), default=str)
    keys = ", ".join(f"{k} = ?" for k in fields)
    values: list[Any] = list(fields.values())
    values.append(run_id)
    with _LOCK:
        c = _conn()
        try:
            c.execute(f"UPDATE runs SET {keys} WHERE id = ?", values)
        finally:
            c.close()


def append_step(run_id: str | None = None, chat_id: str | None = None,
                 layer: str = "outer", idx: int | None = None,
                 tool: str | None = None, args: Any = None,
                 result_ok: bool | None = None,
                 result_preview: str | None = None,
                 reasoning: str | None = None,
                 ts: float | None = None) -> None:
    init()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO steps "
                "(run_id, chat_id, layer, idx, tool, args_json, result_ok, "
                " result_preview, reasoning, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, chat_id, layer, idx, tool,
                 json.dumps(args, default=str) if args is not None else None,
                 int(result_ok) if result_ok is not None else None,
                 result_preview, reasoning, ts or time.time()))
        finally:
            c.close()


def record_proposal(skill: str, kind: str, diff: str | None = None,
                     rationale: str | None = None,
                     file_path: str | None = None,
                     run_id: str | None = None) -> str:
    init()
    pid = f"{int(time.time())}-{kind}-{uuid.uuid4().hex[:8]}"
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO proposals "
                "(id, run_id, skill, kind, file_path, diff, rationale, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, run_id, skill, kind, file_path, diff, rationale,
                 "pending", time.strftime("%Y-%m-%d %H:%M:%S")))
        finally:
            c.close()
    return pid


def update_proposal_status(proposal_id: str, status: str,
                            applied_by: str | None = None,
                            note: str | None = None) -> None:
    """Set proposals.status + applied_at + applied_by. ``note`` is
    appended to rationale for audit (preserves the original)."""
    init()
    fields = ["status = ?"]
    args: list[Any] = [status]
    if status in ("applied", "reverted"):
        fields.append("applied_at = ?")
        args.append(time.strftime("%Y-%m-%d %H:%M:%S"))
    if applied_by is not None:
        fields.append("applied_by = ?")
        args.append(applied_by)
    if note:
        fields.append("rationale = COALESCE(rationale, '') || ? ")
        args.append(f"\n[{status}] {note}")
    args.append(proposal_id)
    with _LOCK:
        c = _conn()
        try:
            c.execute(f"UPDATE proposals SET {', '.join(fields)} "
                       f"WHERE id = ?", args)
        finally:
            c.close()


def record_bench(skill: str, model: str, seeds_passed: int, seeds_total: int,
                  avg_tool_calls: float | None = None,
                  commit_sha: str | None = None,
                  tag: str | None = None) -> None:
    init()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO benches "
                "(skill, model, seeds_passed, seeds_total, avg_tool_calls, run_at, commit_sha, tag) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (skill, model, seeds_passed, seeds_total, avg_tool_calls,
                 time.strftime("%Y-%m-%d %H:%M:%S"), commit_sha, tag))
        finally:
            c.close()


def record_vla_episode(run_id: str, skill: str, success: bool,
                        frames_dir: str | None = None,
                        action_jsonl: str | None = None,
                        language: str | None = None) -> None:
    init()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO vla_episodes "
                "(run_id, skill, success, frames_dir, action_jsonl, language, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, skill, int(success), frames_dir, action_jsonl, language,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
        finally:
            c.close()


# ── reads ─────────────────────────────────────────────────────────────────

def get_run(run_id: str) -> dict[str, Any] | None:
    init()
    c = _conn()
    try:
        r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def list_runs(limit: int = 100, skill: str | None = None,
               outcome: str | None = None, chat_id: str | None = None,
               status: str | None = None) -> list[dict[str, Any]]:
    init()
    q = "SELECT * FROM runs WHERE 1=1"
    args: list[Any] = []
    if skill: q += " AND skill = ?"; args.append(skill)
    if outcome: q += " AND outcome = ?"; args.append(outcome)
    if chat_id: q += " AND chat_id = ?"; args.append(chat_id)
    if status: q += " AND status = ?"; args.append(status)
    q += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)
    c = _conn()
    try:
        return [dict(r) for r in c.execute(q, args).fetchall()]
    finally:
        c.close()


def list_steps(run_id: str | None = None, chat_id: str | None = None,
                layer: str | None = None, since_ts: float = 0.0,
                limit: int = 1000) -> list[dict[str, Any]]:
    init()
    q = "SELECT * FROM steps WHERE ts > ?"
    args: list[Any] = [since_ts]
    if run_id: q += " AND run_id = ?"; args.append(run_id)
    if chat_id: q += " AND chat_id = ?"; args.append(chat_id)
    if layer: q += " AND layer = ?"; args.append(layer)
    q += " ORDER BY ts ASC, id ASC LIMIT ?"
    args.append(limit)
    c = _conn()
    try:
        return [dict(r) for r in c.execute(q, args).fetchall()]
    finally:
        c.close()


def list_proposals(skill: str | None = None,
                    status: str | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
    init()
    q = "SELECT * FROM proposals WHERE 1=1"
    args: list[Any] = []
    if skill: q += " AND skill = ?"; args.append(skill)
    if status: q += " AND status = ?"; args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    c = _conn()
    try:
        return [dict(r) for r in c.execute(q, args).fetchall()]
    finally:
        c.close()


def skill_success_rate(skill: str, since: str | None = None,
                        min_runs: int = 1) -> tuple[float | None, int]:
    """Returns (rate, n_runs). rate is None if n_runs < min_runs."""
    init()
    q = ("SELECT COUNT(*) AS total, "
         " SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS succ "
         "FROM runs WHERE skill = ?")
    args: list[Any] = [skill]
    if since:
        q += " AND started_at >= ?"
        args.append(since)
    c = _conn()
    try:
        r = c.execute(q, args).fetchone()
        total = r["total"] or 0
        if total < min_runs:
            return None, total
        return (r["succ"] or 0) / total, total
    finally:
        c.close()


# ── events (chat-level pub log) ───────────────────────────────────────────

def append_event(chat_id: str, kind: str, payload: dict[str, Any],
                  ts: float | None = None) -> int:
    """Append a chat event; returns the row id (used as cursor `idx`)."""
    init()
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO events (chat_id, kind, payload_json, ts) "
                "VALUES (?,?,?,?)",
                (chat_id, kind,
                 json.dumps(payload, default=str, ensure_ascii=False),
                 ts or time.time()))
            return cur.lastrowid
        finally:
            c.close()


def list_events(chat_id: str, since_id: int = 0,
                 limit: int = 1000) -> list[dict[str, Any]]:
    """Read chat events with cursor pagination. Each returned event has
    {idx, t, kind, ...payload} matching the in-memory LiveSession format."""
    init()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM events WHERE chat_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (chat_id, since_id, limit)).fetchall()
    finally:
        c.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {}
        evt = {"idx": r["id"], "t": r["ts"], "kind": r["kind"], **payload}
        out.append(evt)
    return out
