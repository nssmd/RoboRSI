"""State machine for task-level LIBERO short Pass@k evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from roborsi.libero.catalog import SHORT_TASK_CATALOG

TaskCategory = Literal[
    "task_success",
    "task_failure",
    "implementation_failure",
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
]

INFRASTRUCTURE = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}
TERMINAL = {"task_success", "task_failure", "implementation_failure"}


@dataclass(frozen=True)
class EpisodeVerdict:
    task_key: str
    seed: int
    category: TaskCategory
    simulator_verdict: str | None
    release_id: str
    attempt: int = 1

    def __post_init__(self) -> None:
        if self.category not in TERMINAL | INFRASTRUCTURE:
            raise ValueError(f"unsupported episode category: {self.category}")
        if self.task_key not in SHORT_TASK_CATALOG:
            raise ValueError(f"task outside LIBERO short catalog: {self.task_key}")
        if self.seed < 0 or self.seed > 9:
            raise ValueError("seed must be in 0..9")
        expected = {
            "task_success": "task_success",
            "task_failure": "task_failure",
        }
        if self.category in expected and self.simulator_verdict != expected[self.category]:
            raise ValueError(f"{self.category} requires matching final simulator verdict")
        if self.category not in expected and self.simulator_verdict is not None:
            raise ValueError("non-simulator categories cannot carry a simulator verdict")


@dataclass
class CampaignState:
    schema: str
    mode: Literal["adaptive", "fixed"]
    task_catalog: list[str]
    seeds: list[int]
    release_history: list[str]
    current_release_id: str
    selfevo_frozen: bool
    records: list[EpisodeVerdict] = field(default_factory=list)
    completed_seeds: list[int] = field(default_factory=list)
    status: Literal["running", "complete", "blocked"] = "running"

    @classmethod
    def new(cls, *, mode: Literal["adaptive", "fixed"], release_id: str) -> "CampaignState":
        if mode not in {"adaptive", "fixed"}:
            raise ValueError("mode must be adaptive or fixed")
        if not release_id.strip():
            raise ValueError("release_id must not be empty")
        return cls(
            schema="roborsi.libero_short_campaign_state.v1",
            mode=mode,
            task_catalog=list(SHORT_TASK_CATALOG),
            seeds=list(range(10)),
            release_history=[release_id],
            current_release_id=release_id,
            selfevo_frozen=mode == "fixed",
        )

    @property
    def solved_tasks(self) -> set[str]:
        return {record.task_key for record in self.records if record.category == "task_success"}

    @property
    def terminal_pairs(self) -> set[tuple[str, int]]:
        return {
            (record.task_key, record.seed)
            for record in self.records
            if record.category in TERMINAL
        }

    @property
    def infrastructure_excluded(self) -> int:
        return sum(record.category in INFRASTRUCTURE for record in self.records)

    def begin_release(self, release_id: str) -> None:
        if self.mode == "fixed":
            return
        if not release_id.strip():
            raise ValueError("release_id must not be empty")
        self.current_release_id = release_id
        if release_id not in self.release_history:
            self.release_history.append(release_id)

    def record(self, verdict: EpisodeVerdict) -> None:
        identity = (
            verdict.task_key,
            verdict.seed,
            verdict.attempt,
            verdict.release_id,
        )
        for existing in self.records:
            old_identity = (
                existing.task_key,
                existing.seed,
                existing.attempt,
                existing.release_id,
            )
            if old_identity == identity:
                if existing != verdict:
                    raise ValueError(f"conflicting episode identity: {identity}")
                return
        if verdict.category in TERMINAL:
            signatures = {
                existing.category
                for existing in self.records
                if existing.task_key == verdict.task_key
                and existing.seed == verdict.seed
                and existing.category in TERMINAL
            }
            adaptive_success_override = (
                self.mode == "adaptive"
                and verdict.category == "task_success"
                and signatures <= {"task_failure", "implementation_failure"}
            )
            if signatures and verdict.category not in signatures and not adaptive_success_override:
                raise ValueError(
                    f"conflicting terminal task/seed verdict: {(verdict.task_key, verdict.seed)}"
                )
        self.records.append(verdict)

    def to_dict(self) -> dict:
        row = asdict(self)
        row["solved_tasks"] = sorted(self.solved_tasks)
        row["infrastructure_excluded"] = self.infrastructure_excluded
        return row


def schedule_round(state: CampaignState, *, seed: int) -> list[str]:
    if seed not in state.seeds:
        raise ValueError(f"seed is outside campaign protocol: {seed}")
    solved = state.solved_tasks
    terminal = state.terminal_pairs
    return [
        task
        for task in state.task_catalog
        if task not in solved and (task, seed) not in terminal
    ]
