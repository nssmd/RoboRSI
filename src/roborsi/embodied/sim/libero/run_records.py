from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable


def classify_infrastructure_exception(exc: BaseException) -> str:
    texts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        texts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    text = " | ".join(texts)
    if any(
        marker in text
        for marker in (
            "APIConnectionError",
            "APITimeoutError",
            "ConnectTimeout",
            "ConnectionError",
            "ConnectError",
            "ReadTimeout",
            "RemoteProtocolError",
            "TimeoutError",
            "incomplete chunked",
            "socket hang up",
        )
    ):
        return "transport_failure"
    if (
        "model_not_supported" in text
        or "requested model is not supported" in text
        or "no model endpoints" in text
        or "Upstream error 402" in text
        or "Error code: 402" in text
        or "PermissionDeniedError" in text
        or "Error code: 403" in text
    ):
        return "provider_failure"
    lower_text = text.lower()
    if (
        "could not process image" in lower_text
        or "input exceeds the context window" in lower_text
    ):
        return "image_failure"
    if any(
        marker in text
        for marker in (
            "Too many open files",
            "EMFILE",
            "Again: Resource temporarily unavailable",
            "ZMQError: Operation cannot be accomplished in current state",
        )
    ):
        return "resource_failure"
    return "implementation_failure"


def _slug(value: str) -> str:
    return value.replace("/", "__").replace(" ", "_")


@dataclass(frozen=True)
class EpisodeIdentity:
    run_id: str
    task_key: str
    seed: int
    shard: int
    attempt: int

    @property
    def key(self) -> str:
        return (
            f"{self.run_id}:{self.task_key}:{self.seed}:"
            f"{self.shard}:{self.attempt}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


def episode_workdir(root: Path, identity: EpisodeIdentity) -> Path:
    return (
        Path(root)
        / _slug(identity.run_id)
        / _slug(identity.task_key)
        / f"seed-{identity.seed}"
        / f"shard-{identity.shard}"
        / f"attempt-{identity.attempt}"
    )


@dataclass(frozen=True)
class EpisodeRecord:
    identity: EpisodeIdentity
    category: str
    success: bool | None
    outcome: str | None
    elapsed_s: float = 0.0
    recorded_at: str | None = None
    tool_calls: int = 0
    physics_ticks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    vlm_calls: int = 0
    unmetered_vlm_calls: int = 0
    vlm_time_s: float = 0.0
    perception_time_s: float = 0.0
    action_time_s: float = 0.0
    recovery_time_s: float = 0.0
    recovery_reviewer_calls: int = 0
    recovery_reviewer_errors: int = 0
    role_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    planner_time_s: float = 0.0
    reviewer_time_s: float = 0.0
    orchestration_time_s: float = 0.0
    code_backed_hit: bool = False
    code_backed_call_count: int = 0
    code_backed_tools: tuple[str, ...] = ()
    video_path: str | None = None
    preview_path: str | None = None
    trajectory_path: str | None = None
    detail: str | None = None
    release_id: str | None = None

    def __post_init__(self) -> None:
        expected = {
            "task_success": True,
            "task_failure": False,
            "implementation_failure": False,
        }
        if self.category in expected and self.success is not expected[self.category]:
            raise ValueError(
                "category/success mismatch: "
                f"category={self.category} success={self.success}"
            )

    def to_dict(self) -> dict:
        row = asdict(self)
        row["identity"] = self.identity.to_dict()
        return row

    @classmethod
    def from_dict(cls, row: dict) -> EpisodeRecord:
        data = dict(row)
        identity = EpisodeIdentity(**data.pop("identity"))
        if "code_backed_tools" in data:
            data["code_backed_tools"] = tuple(data["code_backed_tools"] or ())
        return cls(identity=identity, **data)


def append_record(path: Path, record: EpisodeRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        stream.flush()
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _attempt_scope(identity: EpisodeIdentity) -> str:
    return (
        f"{identity.run_id}:{identity.task_key}:"
        f"{int(identity.seed)}:{int(identity.shard)}"
    )


def _attempt_sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".attempts.json")


def reserve_attempt(
    path: Path,
    base_identity: EpisodeIdentity,
    *,
    resume_records: Iterable[EpisodeRecord] | None = None,
) -> EpisodeIdentity:
    """Atomically reserve the next attempt number for one task/seed/shard scope."""
    journal = Path(path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    lock_path = journal.with_suffix(journal.suffix + ".attempt.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        sidecar_path = _attempt_sidecar_path(journal)
        sidecar: dict[str, int] = {}
        if sidecar_path.exists():
            try:
                raw = json.loads(sidecar_path.read_text())
                if isinstance(raw, dict):
                    sidecar = {str(k): int(v) for k, v in raw.items()}
            except Exception:  # noqa: BLE001
                sidecar = {}
        scope = _attempt_scope(base_identity)
        max_journal_attempt = max(
            (
                int(row.identity.attempt)
                for row in load_records(journal)
                if _attempt_scope(row.identity) == scope
            ),
            default=0,
        )
        max_resume_attempt = max(
            (
                int(row.identity.attempt)
                for row in (resume_records or [])
                if _attempt_scope(row.identity) == scope
            ),
            default=0,
        )
        next_attempt = (
            max(max_journal_attempt, int(sidecar.get(scope, 0)), max_resume_attempt) + 1
        )
        sidecar[scope] = int(next_attempt)
        tmp = sidecar_path.with_name(f".{sidecar_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(sidecar, sort_keys=True))
        os.replace(tmp, sidecar_path)
        return replace(base_identity, attempt=int(next_attempt))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_records(path: Path) -> list[EpisodeRecord]:
    p = Path(path)
    if not p.exists():
        return []
    fd = os.open(p, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            rows = [
                json.loads(line)
                for line in stream.read().splitlines()
                if line.strip()
            ]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return [EpisodeRecord.from_dict(row) for row in rows]


def merge_records(
    records: Iterable[EpisodeRecord],
) -> tuple[dict[str, EpisodeRecord], set[str]]:
    """Deduplicate identical identities and surface conflicts.

    Conflicting duplicates are excluded from the output index and returned as keys.
    """
    out: dict[str, EpisodeRecord] = {}
    conflicts: set[str] = set()
    for record in records:
        key = record.identity.key
        if key in conflicts:
            continue
        old = out.get(key)
        if old is None:
            out[key] = record
            continue
        if old != record:
            conflicts.add(key)
            out.pop(key, None)
    return out, conflicts
