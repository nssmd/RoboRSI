"""Pure data readers for the unified board web dashboards (no FastAPI import).

Every function here reads existing on-disk state and returns plain dicts/lists.
Kept import-light so it is unit-testable without the ``[web]`` extra. The web
apps (:mod:`roborsi.embodied.board.web.cockpit_app` / ``evo_app``) are dumb
HTTP wrappers over these functions. The evo-page-specific readers (call chain,
frames, manager_chat) live in :mod:`roborsi.embodied.board.web.evo_readers`.

Data sources
------------
- ``~/.roborsi/agent_sessions.json`` — ``{"role:task": thread_id}``.  This is
  the authoritative session list.
- Claude transcript ``~/.claude/projects/<cwd-slug>/<thread_id>.jsonl`` — the
  multi-turn conversation for a thread.  ``<cwd-slug>`` is the repo root path
  with every non-alphanumeric char turned into ``-`` (Claude Code's convention).
- ``~/.roborsi/trace.db`` — read through :mod:`roborsi.store.trace_db`.
- ``/tmp/pb/{campaign.log,current.txt,current_b.txt}`` — lane A campaign progress.
- ``/tmp/pb/{campaign_b.log,current_b.txt}`` — lane B campaign progress.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from roborsi.store import trace_db

# Repo root = five levels above this file
# (roborsi/embodied/board/web/readers.py).
REPO_ROOT = Path(__file__).resolve().parents[4]

CAMPAIGN_DIR = Path(os.environ.get("ROBORSI_CAMPAIGN_DIR", "/tmp/pb"))

# Self-evolution on-disk state (same sources as scripts/evo_dashboard.py):
#   atomic skills tree — each task's zeroshot/wiki.md accretes Manager-approved
#   leads + success/fail traces; the review queues hold the pending→approved/
#   rejected funnel of failure hypotheses / plan promotions / skill diffs.
ATOMIC_DIR = REPO_ROOT / "roborsi" / "embodied" / "skills" / "atomic"
ROBORSI_HOME = Path(os.environ.get(
    "ROBORSI_HOME", str(Path.home() / ".roborsi")))
REVIEW_QUEUES = ("wiki_review", "skill_review", "plan_review")

# A predicate-verified success: the sim's predicate check truly passed (not
# merely VLM-declared). One marker string drives both the SQL LIKE filter and
# the in-Python check so the two can never drift.
_PREDICATE_MARKER = '"predicate_check": true'
_SUCCESS_PREDICATE = f"%{_PREDICATE_MARKER}%"


def _is_verified_success(run: dict[str, Any]) -> bool:
    """True when a run row is a predicate-verified success."""
    if run.get("status") != "success":
        return False
    return _PREDICATE_MARKER in (run.get("episode_summary_json") or "")


def _query(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    """Run a read query on trace.db through the store's WAL-tuned connection
    factory and return rows as plain dicts. Centralises the init/open/close
    lifecycle so the readers below stay one-liners."""
    trace_db.init()
    conn = trace_db._conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# session_sessions.json
# ---------------------------------------------------------------------------

def _sessions_path() -> Path:
    return Path(os.environ.get(
        "ROBORSI_AGENT_SESSIONS",
        str(Path.home() / ".roborsi" / "agent_sessions.json")))


def _load_session_map() -> dict[str, str]:
    """Return the raw ``{"role:task": thread_id}`` map, or ``{}`` if absent."""
    path = _sessions_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def _split_key(key: str) -> tuple[str, str]:
    """``"planner:handover_block"`` → ``("planner", "handover_block")``.

    Task names never contain ``:`` so a single split on the first colon is
    unambiguous; keys without a colon degrade to role="" / task=key.
    """
    role, sep, task = key.partition(":")
    return (role, task) if sep else ("", key)


# ---------------------------------------------------------------------------
# Claude transcript location
# ---------------------------------------------------------------------------

def cwd_slug(path: Path | str) -> str:
    """Claude Code's project-dir slug: every non-alphanumeric char → ``-``.

    ``/path/to/RoboRSI`` → ``-path-to-RoboRSI``.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _project_dir() -> Path:
    return Path.home() / ".claude" / "projects" / cwd_slug(REPO_ROOT)


def transcript_path(thread_id: str) -> Path | None:
    """Locate ``<thread_id>.jsonl`` under the repo's Claude project dir."""
    if not thread_id:
        return None
    candidate = _project_dir() / f"{thread_id}.jsonl"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Transcript parsing — jsonl of {type, message:{role, content}, timestamp}
# ---------------------------------------------------------------------------

def _stringify_part(part: dict[str, Any]) -> str:
    """One content block → display text (or '' to drop it)."""
    kind = part.get("type")
    if kind == "text":
        return str(part.get("text") or "")
    if kind == "thinking":
        body = str(part.get("thinking") or "").strip()
        return f"[thinking]\n{body}" if body else ""
    if kind == "tool_use":
        args = json.dumps(part.get("input") or {}, ensure_ascii=False)
        return f"[tool: {part.get('name')}] {args}"
    if kind == "tool_result":
        raw = part.get("content")
        body = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        return f"[tool_result]\n{body}"
    return ""


def _message_text(message: dict[str, Any]) -> str:
    """A Claude message's content → a single display string.

    Content is either a plain string or a list of typed blocks (text / thinking
    / tool_use / tool_result).
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    blocks = [_stringify_part(p) for p in (content or []) if isinstance(p, dict)]
    return "\n\n".join(b for b in blocks if b)


def _has_real_content(message: dict[str, Any]) -> bool:
    """A user turn carrying only tool_result blocks is agent plumbing, not a
    human/task message — drop it from the conversation view."""
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    for part in content or []:
        if isinstance(part, dict) and part.get("type") != "tool_result":
            return True
    return False


def parse_transcript(path: Path) -> list[dict[str, Any]]:
    """Parse a transcript jsonl into ordered ``{role, text, ts}`` turns.

    Only ``user`` / ``assistant`` rows become turns; the many ``mode`` /
    ``queue-operation`` / ``attachment`` bookkeeping rows are skipped. User
    turns that carry only tool results (no human/task text) are also skipped so
    the view reads as a real conversation.
    """
    turns: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        row = json.loads(stripped)
        role = row.get("type")
        if role not in ("user", "assistant"):
            continue
        message = row.get("message") or {}
        if role == "user" and not _has_real_content(message):
            continue
        text = _message_text(message)
        if not text.strip():
            continue
        turns.append({"role": role, "text": text, "ts": row.get("timestamp")})
    return turns


# ---------------------------------------------------------------------------
# trace.db — per-task success
# ---------------------------------------------------------------------------

def _task_success_map() -> dict[str, int]:
    """``{task: predicate_verified_success_count}`` over the whole runs table.

    A single grouped query keeps the sessions endpoint cheap regardless of run
    volume (the table can hold thousands of rows).
    """
    rows = _query(
        "SELECT task, COUNT(*) AS n FROM runs "
        "WHERE status = 'success' AND episode_summary_json LIKE ? "
        "GROUP BY task",
        (_SUCCESS_PREDICATE,),
    )
    return {r["task"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Public API — sessions
# ---------------------------------------------------------------------------

def list_sessions() -> list[dict[str, Any]]:
    """All (role, task) sessions with thread_id, last-active mtime and whether
    the task has any predicate-verified success. Sorted newest-active first."""
    session_map = _load_session_map()
    success = _task_success_map()
    out: list[dict[str, Any]] = []
    for key, thread_id in session_map.items():
        row = _session_row(key, thread_id)
        count = success.get(row["task"], 0)
        row["task_success"] = count > 0
        row["success_count"] = count
        out.append(row)
    out.sort(key=lambda s: (s["last_active"] or 0), reverse=True)
    return out


def session_turns(key: str) -> dict[str, Any]:
    """Multi-turn conversation for a session key. Returns metadata + turns."""
    session_map = _load_session_map()
    if key not in session_map:
        return {"found": False, "key": key, "turns": []}
    role, task = _split_key(key)
    thread_id = session_map[key]
    path = transcript_path(thread_id)
    turns = parse_transcript(path) if path else []
    return {
        "found": True,
        "key": key,
        "role": role,
        "task": task,
        "thread_id": thread_id,
        "transcript_path": str(path) if path else None,
        "turns": turns,
    }


# ---------------------------------------------------------------------------
# Public API — task progress
# ---------------------------------------------------------------------------

def task_progress(task: str, limit: int = 50) -> dict[str, Any]:
    """Runs for a task: totals, verified-success count, and a recent slice."""
    runs = _query(
        "SELECT * FROM runs WHERE task = ? ORDER BY started_at DESC LIMIT ?",
        (task, limit),
    )
    total = _query("SELECT COUNT(*) AS n FROM runs WHERE task = ?", (task,))[0]["n"]
    recent = [{
        "id": r["id"],
        "seed": r["seed"],
        "status": r["status"],
        "outcome": r["outcome"],
        "verified": _is_verified_success(r),
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "wallclock_s": r["wallclock_s"],
    } for r in runs]
    return {
        "task": task,
        "total_runs": total,
        "shown": len(recent),
        "verified_success": sum(1 for r in recent if r["verified"]),
        "recent": recent,
    }


def task_overview(limit: int = 40) -> list[dict[str, Any]]:
    """Per-task run tally (total / verified success) across the whole DB."""
    rows = _query(
        "SELECT task, COUNT(*) AS total, "
        " SUM(CASE WHEN status='success' AND episode_summary_json LIKE ? "
        "     THEN 1 ELSE 0 END) AS verified "
        "FROM runs GROUP BY task ORDER BY total DESC LIMIT ?",
        (_SUCCESS_PREDICATE, limit),
    )
    return [{
        "task": r["task"],
        "total": r["total"],
        "verified_success": r["verified"] or 0,
    } for r in rows]


# ---------------------------------------------------------------------------
# Public API — campaign (two lanes: A=GPU1, B=GPU0)
# ---------------------------------------------------------------------------

_CAMPAIGN_LINE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s*(?P<body>.*)$")

# One campaign daemon "lane": its live-cursor file + its append-only log. Lane A
# (the primary, GPU1) is what the WS streams; lane B (GPU0) is the second daemon.
LANES = (
    {"id": "A", "gpu": "1", "current": "current.txt", "log": "campaign.log"},
    {"id": "B", "gpu": "0", "current": "current_b.txt", "log": "campaign_b.log"},
)

# Grammar of a campaign log body (after the "[ts] " prefix is stripped):
#   "✗ <task> seed=<n> no Sim success ... · bot> ..."       rollout outcome
#   "✔ <task> seed=<n> ..." / "... Sim success ..."          verified win
#   "===== ... CAMPAIGN START gpu=<g> tasks=<a b c> ====="   roster
_ROLLOUT_DONE = re.compile(r"^(?P<mark>[✗✔])\s+(?P<task>\S+)\s+seed=(?P<seed>\d+)\b")
_ROSTER = re.compile(r"CAMPAIGN START\s+gpu=(?P<gpu>\S+)\s+tasks=(?P<tasks>.+?)\s*=====\s*$")


def _read_text(name: str) -> str:
    path = CAMPAIGN_DIR / name
    return path.read_text().strip() if path.exists() else ""


def campaign_log_lines(offset: int = 0, tail: int = 200, log: str = "campaign.log") -> dict[str, Any]:
    """Return ``<log>`` lines from ``offset`` (line index), tail-capped.

    ``offset`` is a 0-based line cursor: the WS/poller passes back the returned
    ``next_offset`` to stream only new lines. ``log`` selects the lane's log file
    (``campaign.log`` for lane A, ``campaign_b.log`` for lane B).
    """
    path = CAMPAIGN_DIR / log
    if not path.exists():
        return {"lines": [], "next_offset": 0, "total": 0}
    all_lines = path.read_text().splitlines()
    total = len(all_lines)
    start = offset if offset > 0 else max(0, total - tail)
    slice_lines = all_lines[start:]
    return {"lines": slice_lines, "next_offset": total, "total": total}


def _log_body(line: str) -> str:
    """Strip the ``[ts] `` prefix and leading whitespace from a log line."""
    match = _CAMPAIGN_LINE.match(line)
    return (match.group("body") if match else line).strip()


def _lane_roster(lines: list[str]) -> list[str]:
    """The task roster of the most-recent CAMPAIGN START in a lane's log."""
    for line in reversed(lines):
        match = _ROSTER.search(line)
        if match:
            return match.group("tasks").split()
    return []


def _lane_recent(lines: list[str], limit: int = 8) -> list[dict[str, Any]]:
    """Recent completed rollouts in a lane, newest first: ``{task, seed, ok}``."""
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        match = _ROLLOUT_DONE.match(_log_body(line))
        if match:
            out.append({"task": match.group("task"), "seed": int(match.group("seed")),
                        "ok": match.group("mark") == "✔"})
            if len(out) >= limit:
                break
    return out


def _lane_snapshot(lane: dict[str, str]) -> dict[str, Any]:
    """One lane's live status: current task/seed/round, roster, recent outcomes.

    ``current`` is read from the lane's live-cursor file (authoritative), with the
    roster and recent-outcome tail parsed from its log so a lane card can show
    what it is working and how the last few seeds went.
    """
    current = _read_text(lane["current"])
    log = campaign_log_lines(offset=0, tail=400, log=lane["log"])
    lines = log["lines"]
    return {
        "id": lane["id"],
        "gpu": lane["gpu"],
        "current": current,
        "roster": _lane_roster(lines),
        "recent": _lane_recent(lines),
        "log_total": log["total"],
    }


def _lanes_snapshot() -> list[dict[str, Any]]:
    """Both campaign daemons' live snapshots. Single call point so campaign_status
    and manager_overview don't each re-read the lane log files."""
    return [_lane_snapshot(lane) for lane in LANES]


def campaign_status() -> dict[str, Any]:
    """Overall campaign progress: both lanes' live status + lane-A log digest.

    Keeps the flat ``current`` / ``current_b`` / ``recent_lines`` fields the
    existing campaign view reads, and adds a structured ``lanes`` list so the
    Manager overview can render a card per daemon.
    """
    log = campaign_log_lines(offset=0, tail=40)
    lanes = _lanes_snapshot()
    return {
        "current": _read_text("current.txt"),
        "current_b": _read_text("current_b.txt"),
        "log_total": log["total"],
        "recent_lines": log["lines"],
        "lanes": lanes,
    }


# ---------------------------------------------------------------------------
# Public API — skill self-evolution (wiki leads + review funnel + trend)
#
# Same on-disk sources as scripts/evo_dashboard.py. The review-queue JSONs carry
# their authoritative ``status`` field *inside* each file (files also get moved
# between root / applied / rejected / … subdirs), so we walk the whole tree and
# bucket by that field rather than trusting the directory a file happens to sit
# in — the two can drift.
# ---------------------------------------------------------------------------

# Status → funnel bucket. "pending" is the queue's live backlog; anything the
# Manager acted on lands in approved/applied or rejected; the remaining
# "reviewed_*"/"resolved_*" tags are closed-without-wiki-change outcomes.
_APPROVED_STATUS = {"approved", "applied"}
_REJECTED_STATUS = {"rejected"}


def _funnel_bucket(status: str) -> str:
    if status in _APPROVED_STATUS:
        return "approved"
    if status in _REJECTED_STATUS:
        return "rejected"
    if status == "pending":
        return "pending"
    return "other"


def _load_json(path: Path) -> dict[str, Any] | None:
    """Read one queue JSON; skip files caught mid-write (unparseable/empty) or
    moved out from under us (the queues are churned live by the Manager, so a
    path yielded by rglob can vanish before we read it)."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _iter_queue(name: str):
    """Yield every parsed proposal dict under a review queue (root + subdirs)."""
    root = ROBORSI_HOME / name
    if not root.is_dir():
        return
    for path in root.rglob("*.json"):
        record = _load_json(path)
        if record is not None:
            yield record


def _wiki_section(md: str, header: str) -> str:
    """Return the body text under ``## <header>`` up to the next ``## ``."""
    marker = f"## {header}"
    if marker not in md:
        return ""
    body = md.split(marker, 1)[1]
    return re.split(r"\n## ", body, maxsplit=1)[0].strip("\n")


def _parse_leads(md: str) -> list[dict[str, Any]]:
    """Parse '## Manager-approved leads' into structured entries.

    Each lead is a top-level ``- [<run_id>] <text>`` bullet optionally followed
    by indented ``  - root_cause: …`` / ``  - approved <ts> · <note>`` lines.
    """
    section = _wiki_section(md, "Manager-approved leads")
    leads: list[dict[str, Any]] = []
    for line in section.splitlines():
        if line.startswith("- ["):
            leads.append(_new_lead(line))
        elif line.startswith("  - ") and leads:
            _attach_lead_detail(leads[-1], line[4:])
        elif line.strip() and leads:
            leads[-1]["text"] += " " + line.strip()
    return leads


def _new_lead(line: str) -> dict[str, Any]:
    head = line[2:]  # drop "- "
    run_id, text = "", head
    if head.startswith("[") and "]" in head:
        run_id, _, text = head[1:].partition("]")
    return {"run_id": run_id.strip(), "text": text.strip(),
            "root_cause": "", "approved": ""}


def _attach_lead_detail(lead: dict[str, Any], detail: str) -> None:
    if detail.startswith("root_cause:"):
        lead["root_cause"] = detail.split(":", 1)[1].strip()
    elif detail.startswith("approved"):
        lead["approved"] = detail[len("approved"):].strip(" ·")
    else:
        lead["text"] += " " + detail.strip()


def _count_leads(md: str) -> int:
    return sum(1 for ln in _wiki_section(md, "Manager-approved leads").splitlines()
               if ln.startswith("- ["))


def _task_wiki(task: str) -> Path:
    return ATOMIC_DIR / task / "zeroshot" / "wiki.md"


def _tasks_with_wiki() -> list[tuple[str, str]]:
    """``[(task, wiki_markdown)]`` for every atomic task that has a wiki.md."""
    if not ATOMIC_DIR.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for tdir in sorted(ATOMIC_DIR.iterdir()):
        wiki = tdir / "zeroshot" / "wiki.md"
        if wiki.is_file():
            out.append((tdir.name, wiki.read_text(encoding="utf-8", errors="replace")))
    return out


def _empty_funnel(*, with_total: bool = False) -> dict[str, int]:
    base = {"pending": 0, "approved": 0, "rejected": 0, "other": 0}
    if with_total:
        base["total"] = 0
    return base


def _scan_queues() -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Single pass over every review queue → ``(per_task, per_queue_totals)``.

    The queue trees hold thousands of JSONs and are polled every few seconds, so
    we walk each file exactly once and accumulate both projections together. Note
    skill_review records carry no ``task`` field, so they land in the per-queue
    totals but (correctly) not in the per-task funnel.
    """
    by_task: dict[str, dict[str, int]] = {}
    totals: dict[str, dict[str, int]] = {}
    for queue in REVIEW_QUEUES:
        acc = _empty_funnel(with_total=True)
        for record in _iter_queue(queue):
            bucket = _funnel_bucket(str(record.get("status") or ""))
            acc[bucket] += 1
            acc["total"] += 1
            task = record.get("task")
            if task:
                by_task.setdefault(task, _empty_funnel())[bucket] += 1
        totals[queue] = acc
    return by_task, totals


def evolution_overview() -> dict[str, Any]:
    """Global self-evolution snapshot: per-task knowledge accretion + the review
    funnel totals + a ranking. Reuses the same on-disk sources as
    scripts/evo_dashboard.py (leads / trace counts / review queues); the
    ``verified_success`` ranking additionally cross-references trace.db."""
    success = _task_success_map()
    funnel, totals = _scan_queues()
    tasks: list[dict[str, Any]] = []
    for task, md in _tasks_with_wiki():
        f = funnel.get(task, _empty_funnel())
        node = {
            "task": task,
            "leads": _count_leads(md),
            "success_traces": md.count("outcome: ✓ success"),
            "fail_traces": md.count("outcome: ✗ failure"),
            "verified_success": success.get(task, 0),
            "hyp_pending": f["pending"],
            "hyp_approved": f["approved"],
            "hyp_rejected": f["rejected"],
        }
        if any(node[k] for k in ("leads", "success_traces", "fail_traces",
                                 "verified_success", "hyp_pending",
                                 "hyp_approved", "hyp_rejected")):
            tasks.append(node)
    tasks.sort(key=lambda t: (t["verified_success"], t["leads"],
                              t["success_traces"], t["hyp_approved"]),
               reverse=True)
    return {"tasks": tasks, "totals": totals, "task_count": len(tasks)}


def _task_hypotheses(task: str, limit: int = 60) -> list[dict[str, Any]]:
    """Failure hypotheses for one task from wiki_review, newest first."""
    rows = [
        {
            "id": r.get("id"),
            "status": r.get("status"),
            "bucket": _funnel_bucket(str(r.get("status") or "")),
            "root_cause": r.get("root_cause") or "",
            "next_action": r.get("next_action") or "",
            "manager_note": r.get("manager_note") or "",
            "created_at": r.get("created_at"),
        }
        for r in _iter_queue("wiki_review") if r.get("task") == task
    ]
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return rows[:limit]


def _task_success_trend(task: str, limit: int = 80) -> list[dict[str, Any]]:
    """Chronological run outcomes for a task (oldest→newest) from trace.db —
    the success/fail-over-time series. Scoped by task in SQL so it works even
    for tasks outside the newest-N runs globally."""
    runs = _query(
        "SELECT * FROM runs WHERE task = ? ORDER BY started_at DESC LIMIT ?",
        (task, limit),
    )
    series = [
        {
            "id": r["id"],
            "seed": r["seed"],
            "status": r["status"],
            "outcome": r["outcome"],
            "verified": _is_verified_success(r),
            "started_at": r["started_at"],
        }
        for r in runs
    ]
    series.sort(key=lambda r: r["started_at"] or "")
    return series


def task_evolution(task: str) -> dict[str, Any]:
    """Single-task self-evolution detail: full Manager-approved leads, the key
    wiki sections, the failure-hypothesis funnel, and the success trend."""
    wiki = _task_wiki(task)
    md = wiki.read_text(encoding="utf-8", errors="replace") if wiki.is_file() else ""
    hyps = _task_hypotheses(task)
    return {
        "task": task,
        "has_wiki": wiki.is_file(),
        "leads": _parse_leads(md),
        "measurements": _wiki_section(md, "Key measurements (Reviewer-proposed, human-approved)"),
        "successful_traces": _wiki_section(md, "Successful execution traces"),
        "success_traces": md.count("outcome: ✓ success"),
        "fail_traces": md.count("outcome: ✗ failure"),
        "hypotheses": hyps,
        "hyp_funnel": {
            "pending": sum(1 for h in hyps if h["bucket"] == "pending"),
            "approved": sum(1 for h in hyps if h["bucket"] == "approved"),
            "rejected": sum(1 for h in hyps if h["bucket"] == "rejected"),
        },
        "trend": _task_success_trend(task),
    }


# ---------------------------------------------------------------------------
# Public API — Manager overview (the orchestration hierarchy, top-down)
#
# The real managers (backend-agnostic top-level sessions) are discovered via
# roborsi.agents.manager.sessions — the same source the CLI picker uses.
# Everything below is what a manager drives: the campaign daemons (two lanes),
# the per-task planner/reviewer role sessions, and the skill self-evolution
# funnel. This reader stitches those into one top-down snapshot so the cockpit
# leads with the Manager(s) instead of a flat session list.
# ---------------------------------------------------------------------------

# Role sessions the Manager orchestrates per task. engineer is a per-seed
# rollout (not a persistent session), so only these two are per-task threads.
_TASK_ROLES = ("planner", "reviewer")


def _session_row(key: str, thread_id: str) -> dict[str, Any]:
    """One session's cockpit row (key/role/task + transcript mtime & turn count).

    Reused by both the flat session list and the Manager's task grouping so the
    two never disagree on a session's shape.
    """
    role, task = _split_key(key)
    path = transcript_path(thread_id)
    return {
        "key": key,
        "role": role,
        "task": task,
        "thread_id": thread_id,
        "last_active": path.stat().st_mtime if path else None,
        "has_transcript": path is not None,
    }


def _queue_pending() -> dict[str, int]:
    """Pending backlog per review queue — the Manager's approval to-do list."""
    _, totals = _scan_queues()
    return {queue: tally["pending"] for queue, tally in totals.items()}


def _manager_task_groups(success: dict[str, int]) -> list[dict[str, Any]]:
    """Tasks the Manager orchestrates, each with its planner/reviewer sessions.

    A task appears when it has at least one persistent planner/reviewer session.
    Each group carries the role sessions, the task's predicate-verified success
    count (``success`` map, trace.db), and its Manager-approved lead count
    (wiki.md) — the "manager → its per-task roles" spine of the overview.
    """
    session_map = _load_session_map()
    by_task: dict[str, list[dict[str, Any]]] = {}
    for key, thread_id in session_map.items():
        role, task = _split_key(key)
        if role in _TASK_ROLES:
            by_task.setdefault(task, []).append(_session_row(key, thread_id))
    groups = [_build_task_group(task, rows, success) for task, rows in by_task.items()]
    groups.sort(key=lambda g: (g["verified_success"], g["last_active"] or 0), reverse=True)
    return groups


def _build_task_group(task: str, rows: list[dict[str, Any]], success: dict[str, int]) -> dict[str, Any]:
    rows.sort(key=lambda r: r["role"])
    wiki = _task_wiki(task)
    leads = _count_leads(wiki.read_text(encoding="utf-8", errors="replace")) if wiki.is_file() else 0
    return {
        "task": task,
        "sessions": rows,
        "verified_success": success.get(task, 0),
        "leads": leads,
        "last_active": max((r["last_active"] or 0 for r in rows), default=0) or None,
    }


def _managers() -> list[dict[str, Any]]:
    """The real manager sessions (backend-agnostic), each with its own transcript.
    Single source of truth is roborsi.agents.manager.sessions — the same list
    the CLI's ``roborsi manager --resume`` picker uses."""
    from roborsi.agents.manager import sessions as msess
    out: list[dict[str, Any]] = []
    for m in msess.list_manager_sessions():
        out.append({
            "id": m.id, "backend": m.backend, "topic": m.topic, "label": m.label,
            "last_active": m.last_active, "turn_count": m.turn_count,
            "recent_turns": msess.manager_turns(m.id, 8),
        })
    return out


def manager_overview() -> dict[str, Any]:
    """Top-down snapshot led by the Manager(s): the real manager sessions + what
    they drive.

    Stitches existing readers (session map, trace.db, review queues, campaign
    logs) into the orchestration hierarchy — Manager → campaign lanes / per-task
    role sessions / skill-evolution funnel. ``managers`` is the list of real
    backend-agnostic manager sessions (this cockpit's campaign lanes/task_groups
    belong to the ``robotwin`` one).
    """
    pending = _queue_pending()
    success = _task_success_map()
    groups = _manager_task_groups(success)
    return {
        "managers": _managers(),
        "totals": {
            "verified_success": sum(success.values()),
            "pending_review": sum(pending.values()),
            "pending_by_queue": pending,
            "orchestrated_tasks": len(groups),
        },
        "lanes": _lanes_snapshot(),
        "task_groups": groups,
    }
