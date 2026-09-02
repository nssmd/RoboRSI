"""Replay adaptive cross-release coverage from append-only public evidence."""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from roborsi.libero.catalog import suite_for, validate_short_catalog

TERMINAL = {"task_success", "task_failure"}
INFRASTRUCTURE = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}
ALLOWED = TERMINAL | INFRASTRUCTURE | {"implementation_failure"}


class EvidenceConflict(ValueError):  # noqa: N818 - public schema terminology
    """Raised when one task/seed has incompatible final verdicts."""


def default_manifest_path() -> Path:
    source = (
        Path(__file__).resolve().parents[3]
        / "evidence/adaptive-coverage-v1/manifest.json"
    )
    if source.is_file():
        return source
    resource = importlib.resources.files("roborsi.libero").joinpath(
        "evidence/adaptive-coverage-v1/manifest.json"
    )
    return Path(str(resource))


@dataclass(frozen=True)
class ReplayResult:
    schema: str
    metric: str
    claim_scope: str
    k: int
    solved_tasks: int
    total_tasks: int
    rate: float
    pass_curve: list[int]
    by_suite: dict[str, dict[str, int | float]]
    task_success_records: int
    task_failure_records: int
    implementation_failures: int
    infrastructure_excluded: int
    total_tokens: int
    median_total_tokens: float
    total_vlm_calls: int
    total_elapsed_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"evidence row must be an object at {path}:{line_number}")
        rows.append(row)
    return rows


def _validate_row(row: dict[str, Any], catalog: set[str]) -> None:
    task = str(row.get("task_key") or "")
    category = str(row.get("category") or "")
    if task not in catalog:
        raise ValueError(f"evidence task is outside the catalog: {task}")
    if category not in ALLOWED:
        raise ValueError(f"unsupported evidence category: {category}")
    if int(row.get("seed", -1)) < 0:
        raise ValueError(f"invalid seed for {task}")
    if category == "task_success" and row.get("simulator_verdict") != "task_success":
        raise ValueError(f"task success lacks final simulator verdict: {task}")
    if category == "task_failure" and row.get("simulator_verdict") != "task_failure":
        raise ValueError(f"task failure lacks final simulator verdict: {task}")


def replay_bundle(manifest_path: Path | str) -> ReplayResult:
    path = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "roborsi.libero_short_evidence.v1":
        raise ValueError("unsupported evidence manifest schema")
    catalog = validate_short_catalog(tuple(manifest.get("task_catalog") or ()))
    k = int(manifest.get("k", 0))
    if k <= 0:
        raise ValueError("evidence k must be positive")
    episodes_path = (path.parent / str(manifest.get("episodes") or "")).resolve()
    rows = _load_jsonl(episodes_path)
    catalog_set = set(catalog)
    for row in rows:
        _validate_row(row, catalog_set)

    identity_rows: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    terminal_signatures: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        identity = (
            str(row["task_key"]),
            int(row["seed"]),
            int(row.get("attempt", 1)),
            str(row.get("release_id") or ""),
        )
        old = identity_rows.get(identity)
        if old is not None and old != row:
            raise EvidenceConflict(f"conflicting duplicate evidence identity: {identity}")
        identity_rows[identity] = row
        if row["category"] in TERMINAL:
            key = (str(row["task_key"]), int(row["seed"]))
            terminal_signatures.setdefault(key, set()).add(str(row["category"]))
    conflicts = {key: values for key, values in terminal_signatures.items() if len(values) > 1}
    if conflicts:
        raise EvidenceConflict(f"conflicting terminal verdicts: {conflicts}")

    unique_rows = list(identity_rows.values())
    round_seeds = [int(value) for value in manifest.get("round_seeds") or range(k)]
    if len(round_seeds) != k or len(round_seeds) != len(set(round_seeds)):
        raise ValueError("round_seeds must contain exactly k unique values")
    solved: set[str] = set()
    pass_curve: list[int] = []
    for seed in round_seeds:
        solved.update(
            str(row["task_key"])
            for row in unique_rows
            if int(row["seed"]) == seed and row["category"] == "task_success"
        )
        pass_curve.append(len(solved))

    by_suite: dict[str, dict[str, int | float]] = {}
    for suite in sorted({suite_for(task) for task in catalog}):
        suite_tasks = {task for task in catalog if suite_for(task) == suite}
        count = len(solved & suite_tasks)
        by_suite[suite] = {
            "solved_tasks": count,
            "total_tasks": len(suite_tasks),
            "rate": count / len(suite_tasks),
        }

    metered_rows = [row for row in unique_rows if row["category"] not in INFRASTRUCTURE]
    tokens = [int(row.get("total_tokens") or 0) for row in metered_rows]
    result = ReplayResult(
        schema="roborsi.libero_short_replay.v1",
        metric=str(
            manifest.get("metric") or "adaptive_cross_release_task_coverage"
        ),
        claim_scope=str(manifest.get("claim_scope") or "unspecified"),
        k=k,
        solved_tasks=len(solved),
        total_tasks=len(catalog),
        rate=len(solved) / len(catalog),
        pass_curve=pass_curve,
        by_suite=by_suite,
        task_success_records=sum(row["category"] == "task_success" for row in unique_rows),
        task_failure_records=sum(row["category"] == "task_failure" for row in unique_rows),
        implementation_failures=sum(
            row["category"] == "implementation_failure" for row in unique_rows
        ),
        infrastructure_excluded=sum(row["category"] in INFRASTRUCTURE for row in unique_rows),
        total_tokens=sum(tokens),
        median_total_tokens=float(median(tokens)) if tokens else 0.0,
        total_vlm_calls=sum(int(row.get("vlm_calls") or 0) for row in metered_rows),
        total_elapsed_s=sum(float(row.get("elapsed_s") or 0.0) for row in metered_rows),
    )
    expected = manifest.get("expected_result")
    if isinstance(expected, dict):
        comparisons = {
            "solved_tasks": result.solved_tasks,
            "total_tasks": result.total_tasks,
        }
        for field, actual in comparisons.items():
            if field in expected and int(expected[field]) != int(actual):
                raise ValueError(
                    f"manifest expected_result mismatch: {field}={expected[field]} actual={actual}"
                )
        if "rate" in expected and abs(float(expected["rate"]) - result.rate) > 1e-12:
            raise ValueError(
                f"manifest expected_result mismatch: rate={expected['rate']} actual={result.rate}"
            )
        breakdown = expected.get("breakdown")
        if isinstance(breakdown, dict):
            actual_breakdown = {
                suite: int(values["solved_tasks"])
                for suite, values in result.by_suite.items()
            }
            if {str(key): int(value) for key, value in breakdown.items()} != actual_breakdown:
                raise ValueError("manifest expected_result mismatch: breakdown")
    return result
