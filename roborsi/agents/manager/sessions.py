"""Manager session registry — the single source of truth for both the CLI
(``roborsi manager --resume``) and the cockpit's manager list.

A *manager session* is a top-level agent session that drives the system (triage,
supervision), as opposed to a per-(role,task) planner/reviewer session. Managers
are backend-agnostic — a manager runs on ``claude``, ``codex``, or ``copilot``
(mirroring the role sessions' ``ROBORSI_ROLE_BACKEND``).

Discovery is REGISTRY-based, not heuristic: the login-home Claude project dir is a
junk drawer of every session ever run there, so we cannot isolate managers by
scanning. Instead ``roborsi manager`` registers each launch here (id +
backend + where its transcript lives), and this module resolves each entry's live
transcript. ``~/.roborsi/manager_sessions.json`` maps
``{id: {backend, cwd_slug}}``; everything else (topic, label, activity) is derived
from the transcript at list time.

Only the ``claude`` backend resolves transcripts today; ``codex`` / ``copilot``
are the extension points in ``_transcript_for``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_TAIL_BYTES = 200_000
_HEAD_BYTES = 60_000
_TOPIC_WORDS = {
    "robotwin": ("robotwin", "campaign", "beat_block_hammer", "place_fan",
                 "adjust_bottle", "place_object", "grasp"),
    "libero": ("libero", "libero-pro", "libero_pick"),
}


@dataclass
class ManagerSession:
    """One manager session, backend-agnostic."""
    id: str
    backend: str            # claude | codex | copilot
    topic: str              # robotwin | libero | other (content fingerprint)
    label: str              # first operator line (or topic)
    path: str
    cwd_slug: str
    cwd: str                # working dir to resume in / locate the transcript
    last_active: float      # transcript mtime (unix)
    turn_count: int


def cwd_slug(path: Path | str) -> str:
    """Claude Code's project-dir slug: every non-alphanumeric char → ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _registry_path() -> Path:
    return Path(os.environ.get(
        "ROBORSI_MANAGER_REGISTRY",
        str(Path.home() / ".roborsi" / "manager_sessions.json")))


def _load_registry() -> dict[str, dict]:
    path = _registry_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def register_manager(session_id: str, *, backend: str, cwd: str) -> None:
    """Record a launched manager session so the CLI picker + cockpit can find it.
    ``cwd`` is the working dir the session runs in — needed to resume it (claude
    ``--resume`` searches the current project dir) and to locate its transcript."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = _load_registry()
    reg[session_id] = {"backend": backend, "cwd": str(cwd),
                       "cwd_slug": cwd_slug(cwd)}
    path.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")


def _transcript_for(session_id: str, meta: dict) -> Path | None:
    """Where a registered manager's transcript lives, by backend."""
    if meta.get("backend") == "claude":
        return (Path.home() / ".claude" / "projects"
                / meta.get("cwd_slug", "") / f"{session_id}.jsonl")
    # codex / copilot extension point — resolve their session stores here.
    return None


def resolve_manager_transcript(session_id: str) -> Path | None:
    meta = _load_registry().get(session_id)
    if meta is None:
        return None
    path = _transcript_for(session_id, meta)
    return path if path and path.exists() else None


def manager_turns(session_id: str, n: int = 8) -> list[dict[str, str]]:
    """Last ``n`` operator/assistant turns of a manager session, from a BOUNDED
    tail read that robustly skips malformed / mid-write lines (the transcript is
    a large, live, append-only jsonl — never parse it whole)."""
    path = resolve_manager_transcript(session_id)
    if path is None:
        return []
    turns: list[dict[str, str]] = []
    for line in _read_bounded(path, head=False).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") or {}
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        text = content if isinstance(content, str) else _join_text(content)
        text = text.strip()
        if text:
            turns.append({"role": role, "text": text[:2000]})
    return turns[-n:]


def list_manager_sessions() -> list[ManagerSession]:
    """All registered manager sessions with a live transcript, newest-active
    first. Registry entries whose transcript is gone are silently skipped."""
    out: list[ManagerSession] = []
    for sid, meta in _load_registry().items():
        path = _transcript_for(sid, meta)
        if path is None or not path.exists():
            continue
        out.append(_build(sid, meta, path))
    out.sort(key=lambda s: s.last_active, reverse=True)
    return out


def _build(sid: str, meta: dict, path: Path) -> ManagerSession:
    label = _label(path)
    topic = _topic(path, label)
    with path.open("rb") as fh:
        turns = sum(1 for _ in fh)
    return ManagerSession(
        id=sid, backend=meta.get("backend", "claude"), topic=topic,
        label=label, path=str(path), cwd_slug=meta.get("cwd_slug", ""),
        cwd=meta.get("cwd", str(Path.home())),
        last_active=path.stat().st_mtime, turn_count=turns)


def _read_bounded(path: Path, *, head: bool) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if head or size <= _TAIL_BYTES:
            data = fh.read(_HEAD_BYTES if head else _TAIL_BYTES)
        else:
            fh.seek(size - _TAIL_BYTES)
            data = fh.read()
    return data.decode("utf-8", errors="replace")


def _topic(path: Path, label: str) -> str:
    """Fingerprint the session's FOUNDING intent. The first operator line (the
    founding task) is decisive when it names a domain; otherwise fall back to a
    head keyword scan. The drifting tail is unreliable — a libero manager still
    reads robotwin campaign refs later on."""
    low = label.lower()
    for topic, words in _TOPIC_WORDS.items():
        if any(w in low for w in words):
            return topic
    text = _read_bounded(path, head=True).lower()
    scores = {k: sum(text.count(w) for w in words) for k, words in _TOPIC_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def _label(path: Path) -> str:
    for line in _read_bounded(path, head=True).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") or {}
        if msg.get("role") == "user":
            content = msg.get("content")
            text = content if isinstance(content, str) else _first_text(content)
            text = " ".join(str(text).split())
            if text:
                return text[:60]
    return ""


def _first_text(content: object) -> str:
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text") or "")
    return ""


def _join_text(content: object) -> str:
    """All text blocks of a message joined (for turn display)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
    return ""
