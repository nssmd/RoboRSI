from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
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


def _loads_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


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
    efficiency_schema: str = ""
    video_path: str | None = None
    preview_path: str | None = None
    trajectory_path: str | None = None
    detail: str | None = None
    model: str | None = None
    served_model: str | None = None
    model_mapping: str | None = None
    vlm_declared: bool | None = None
    code_fingerprint: str | None = None
    config_fingerprint: str | None = None

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


class RecordConflict(ValueError):
    pass


def append_record(path: Path, record: EpisodeRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record.to_dict(), sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        original_end = os.lseek(fd, 0, os.SEEK_END)
        view = memoryview(payload)
        written = 0
        try:
            while written < len(payload):
                n = os.write(fd, view[written:])
                if n <= 0:
                    raise OSError("short write: os.write returned 0")
                written += int(n)
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, original_end)
            os.fsync(fd)
            raise
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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


def index_records(records: Iterable[EpisodeRecord]) -> dict[str, EpisodeRecord]:
    out: dict[str, EpisodeRecord] = {}
    for record in records:
        old = out.get(record.identity.key)
        if old is None:
            out[record.identity.key] = record
            continue
        if old != record:
            raise RecordConflict(record.identity.key)
    return out


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


_TASK_CATEGORIES = {
    "task_success",
    "task_failure",
    "implementation_failure",
}
_INFRA_CATEGORIES = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}


def build_task_summary(
    records: Iterable[EpisodeRecord],
    *,
    expected_seeds: int,
    conflicts: set[str] | None = None,
) -> dict[str, dict]:
    conflict_counts: dict[str, int] = {}
    for key in conflicts or set():
        parts = key.split(":")
        if len(parts) >= 5:
            task_key = parts[1]
            conflict_counts[task_key] = conflict_counts.get(task_key, 0) + 1
    out: dict[str, dict] = {}
    observed_by_task: dict[str, set[int]] = {}
    terminal_by_task_seed: dict[str, dict[int, set[tuple[str, bool]]]] = {}
    for record in records:
        task_key = record.identity.task_key
        row = out.setdefault(
            task_key,
            {
                "task_key": task_key,
                "successes": 0,
                "total": 0,
                "success_rate": 0.0,
                "expected_seeds": int(expected_seeds),
                "observed_seeds": 0,
                "infrastructure_excluded": 0,
                "implementation_failures": 0,
                "conflicts": 0,
            },
        )
        observed_by_task.setdefault(task_key, set()).add(int(record.identity.seed))
        if record.category in _TASK_CATEGORIES:
            per_seed = terminal_by_task_seed.setdefault(task_key, {})
            seed_sigs = per_seed.setdefault(int(record.identity.seed), set())
            seed_sigs.add((str(record.category), bool(record.success)))
        elif record.category in _INFRA_CATEGORIES:
            row["infrastructure_excluded"] += 1
    for task_key, row in out.items():
        terminal_conflicts = 0
        for _seed, sigs in terminal_by_task_seed.get(task_key, {}).items():
            task_sigs = {
                sig
                for sig in sigs
                if sig[0] in {"task_success", "task_failure"}
            }
            if task_sigs:
                if len(task_sigs) != 1:
                    terminal_conflicts += 1
                    continue
                category, _success = next(iter(task_sigs))
            else:
                category = "implementation_failure"
            row["total"] += 1
            if category == "task_success":
                row["successes"] += 1
            elif category == "implementation_failure":
                row["implementation_failures"] += 1
        row["observed_seeds"] = len(observed_by_task.get(task_key, set()))
        total = int(row["total"])
        row["success_rate"] = float(row["successes"] / total) if total else 0.0
        row["conflicts"] = int(conflict_counts.get(task_key, 0)) + int(terminal_conflicts)
    return out


def successful_seed_identities(records: Iterable[EpisodeRecord]) -> set[tuple[str, int]]:
    return {
        (r.identity.task_key, int(r.identity.seed))
        for r in records
        if r.category == "task_success"
    }


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    created_at: str
    arm: str
    sets: str
    workers: int
    seed_start: int
    seeds: int
    model: str
    output_root: Path
    git_head: str
    dirty_hashes: dict[str, str]
    requested_fd_limit: int
    effective_soft_fd_limit: int
    effective_hard_fd_limit: int
    fd_limit_verified: bool
    endpoint: str = ""
    media_root: str = ""
    perception: str = ""
    controller: str = ""
    collection: str = ""
    max_probe_latency_s: float | None = None
    protocol: str = "libero_integrity_repair_v1"
    init_state_root: str = ""
    selfevo_frozen: bool = False
    atomic_compound_enabled: bool = True
    reasoning_effort: str = ""
    roles_enabled: bool = False
    task_keys: tuple[str, ...] = ()
    expected_task_seed_pairs: int = 0
    residual_task_seed_pairs: int = 0
    batch_class: str = "strict_clean"
    blocked_keys_snapshot: str = ""
    resume_journal_snapshot: str = ""
    resume_scope_snapshot: str = ""
    release_id: str = ""
    shards: int | None = None
    horizon: int | None = None
    image_size: tuple[int, int] = ()
    tool_budget: int = 0
    max_new_terminals: int = 0

    def to_dict(self) -> dict:
        row = asdict(self)
        row["output_root"] = str(self.output_root)
        return row

    @classmethod
    def from_dict(cls, row: dict) -> RunManifest:
        data = dict(row)
        data["output_root"] = Path(data["output_root"])
        if "task_keys" in data:
            data["task_keys"] = tuple(data["task_keys"])
        if "image_size" in data:
            data["image_size"] = tuple(data["image_size"] or ())
        return cls(**data)


def build_manifest(
    *,
    root: Path,
    arm: str,
    sets: str,
    workers: int,
    seed_start: int,
    seeds: int,
    model: str,
    run_id: str,
    git_head: str = "unknown",
    dirty_hashes: dict[str, str] | None = None,
    requested_fd_limit: int = 0,
    effective_fd_limits: tuple[int, int] = (0, 0),
    fd_limit_verified: bool = False,
    endpoint: str = "",
    media_root: str = "",
    perception: str = "",
    controller: str = "",
    horizon: int | None = None,
    image_size: tuple[int, int] = (),
    collection: str = "",
    max_probe_latency_s: float | None = None,
    protocol: str = "libero_integrity_repair_v1",
    init_state_root: str = "",
    selfevo_frozen: bool = False,
    atomic_compound_enabled: bool = True,
    reasoning_effort: str = "",
    roles_enabled: bool = False,
    task_keys: tuple[str, ...] = (),
    expected_task_seed_pairs: int = 0,
    residual_task_seed_pairs: int = 0,
    batch_class: str = "strict_clean",
    blocked_keys_snapshot: str = "",
    resume_journal_snapshot: str = "",
    resume_scope_snapshot: str = "",
    release_id: str = "",
    tool_budget: int = 0,
    max_new_terminals: int = 0,
) -> RunManifest:
    output_root = Path(root) / f"libero_{arm}_{sets}" / run_id
    return RunManifest(
        run_id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        arm=arm,
        sets=sets,
        workers=workers,
        seed_start=seed_start,
        seeds=seeds,
        model=model,
        output_root=output_root,
        git_head=git_head,
        dirty_hashes=dirty_hashes or {},
        requested_fd_limit=int(requested_fd_limit),
        effective_soft_fd_limit=int(effective_fd_limits[0]),
        effective_hard_fd_limit=int(effective_fd_limits[1]),
        fd_limit_verified=bool(fd_limit_verified),
        endpoint=str(endpoint),
        media_root=str(media_root),
        perception=str(perception),
        controller=str(controller),
        horizon=int(horizon) if horizon is not None else None,
        image_size=tuple(int(value) for value in image_size),
        collection=str(collection),
        max_probe_latency_s=(
            float(max_probe_latency_s)
            if max_probe_latency_s is not None
            else None
        ),
        protocol=str(protocol),
        init_state_root=str(init_state_root),
        selfevo_frozen=bool(selfevo_frozen),
        atomic_compound_enabled=bool(atomic_compound_enabled),
        reasoning_effort=str(reasoning_effort),
        roles_enabled=bool(roles_enabled),
        task_keys=tuple(str(key) for key in task_keys),
        expected_task_seed_pairs=int(expected_task_seed_pairs),
        residual_task_seed_pairs=int(residual_task_seed_pairs),
        batch_class=str(batch_class),
        blocked_keys_snapshot=str(blocked_keys_snapshot),
        resume_journal_snapshot=str(resume_journal_snapshot),
        resume_scope_snapshot=str(resume_scope_snapshot),
        release_id=str(release_id),
        shards=workers,
        tool_budget=int(tool_budget),
        max_new_terminals=int(max_new_terminals),
    )
