"""Read-only discovery and normalization of local RoboRSI campaigns."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roborsi_libero.catalog import SHORT_TASK_CATALOG, suite_for

INFRASTRUCTURE = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}


@dataclass(frozen=True)
class CampaignEntry:
    run_id: str
    path: Path
    created_at: str
    mode: str
    status: str
    solved_tasks: int
    total_tasks: int
    completed_passes: int
    protocol_passes: int


def _read_object(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"missing campaign file: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return payload


def _campaign_files(campaign_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(campaign_root).expanduser().resolve()
    manifest = _read_object(root / "manifest.json")
    if manifest.get("schema") != "roborsi.libero_short_campaign.v1":
        raise ValueError(f"unsupported campaign manifest: {root / 'manifest.json'}")
    state = _read_object(root / "state.json", required=False)
    result = _read_object(root / "result.json", required=False)
    return manifest, state, result


def _journal_records(campaign_root: Path) -> list[dict[str, Any]]:
    records: dict[tuple[str, str, int, int, int], dict[str, Any]] = {}
    journals = campaign_root / "journals"
    if not journals.is_dir():
        return []
    for path in sorted(journals.glob("*.episodes.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid campaign journal at {path.name}:{line_number}") from exc
            if not isinstance(row, dict) or not isinstance(row.get("identity"), dict):
                raise ValueError(f"invalid campaign journal row at {path.name}:{line_number}")
            identity = row["identity"]
            key = (
                str(identity.get("run_id") or ""),
                str(identity.get("task_key") or ""),
                int(identity.get("seed", -1)),
                int(identity.get("shard", -1)),
                int(identity.get("attempt", -1)),
            )
            old = records.get(key)
            if old is not None and old != row:
                raise ValueError(f"conflicting campaign journal identity: {key}")
            records[key] = row
    return list(records.values())


def _flat_record(row: dict[str, Any]) -> dict[str, Any]:
    identity = row.get("identity")
    if isinstance(identity, dict):
        return {
            **row,
            "task_key": str(identity.get("task_key") or ""),
            "seed": int(identity.get("seed", -1)),
        }
    return row


def _entry(campaign_root: Path) -> CampaignEntry:
    root = Path(campaign_root).expanduser().resolve()
    manifest, state, result = _campaign_files(root)
    solved_value = result.get("solved_tasks", state.get("solved_tasks", []))
    solved = len(solved_value) if isinstance(solved_value, list) else int(solved_value or 0)
    completed = result.get("completed_seeds", state.get("completed_seeds", []))
    seeds = list(manifest.get("seeds") or ())
    return CampaignEntry(
        run_id=str(manifest.get("run_id") or root.name),
        path=root,
        created_at=str(manifest.get("created_at") or ""),
        mode=str(manifest.get("mode") or "unknown"),
        status=str(result.get("status") or state.get("status") or "created"),
        solved_tasks=solved,
        total_tasks=int(manifest.get("task_count") or len(SHORT_TASK_CATALOG)),
        completed_passes=len(completed) if isinstance(completed, list) else 0,
        protocol_passes=len(seeds),
    )


def discover_campaigns(results_root: Path | str) -> list[CampaignEntry]:
    """Return valid campaigns below a result root, newest first."""
    root = Path(results_root).expanduser().resolve()
    if not root.is_dir():
        return []
    campaigns: list[CampaignEntry] = []
    for path in root.iterdir():
        if not path.is_dir() or not (path / "manifest.json").is_file():
            continue
        try:
            campaigns.append(_entry(path))
        except (OSError, ValueError):
            continue
    return sorted(
        campaigns,
        key=lambda campaign: (campaign.created_at, campaign.run_id),
        reverse=True,
    )


def resolve_campaign(
    results_root: Path | str,
    reference: Path | str | None = None,
) -> Path:
    """Resolve an explicit campaign path/id or select the latest campaign."""
    root = Path(results_root).expanduser().resolve()
    if reference is None:
        campaigns = discover_campaigns(root)
        if not campaigns:
            raise FileNotFoundError(f"no RoboRSI campaigns found in {root}")
        return campaigns[0].path

    supplied = Path(reference).expanduser()
    direct = supplied.resolve()
    candidate = direct if direct.is_dir() else (root / supplied).resolve()
    if not candidate.is_dir() or not (candidate / "manifest.json").is_file():
        raise FileNotFoundError(f"campaign not found: {reference}")
    _campaign_files(candidate)
    return candidate


def _suite_summary(solved: set[str]) -> dict[str, dict[str, int | float]]:
    summary: dict[str, dict[str, int | float]] = {}
    for suite in sorted({suite_for(task) for task in SHORT_TASK_CATALOG}):
        tasks = {task for task in SHORT_TASK_CATALOG if suite_for(task) == suite}
        count = len(tasks & solved)
        summary[suite] = {
            "solved_tasks": count,
            "total_tasks": len(tasks),
            "rate": count / len(tasks),
        }
    return summary


def _state_pass_curve(
    records: list[dict[str, Any]],
    seeds: list[int],
    completed_seeds: list[int],
) -> list[int]:
    observed = [int(row.get("seed", -1)) for row in records if int(row.get("seed", -1)) >= 0]
    observed_count = max(
        [len(completed_seeds), *(seed + 1 for seed in observed)],
        default=0,
    )
    cumulative: set[str] = set()
    curve: list[int] = []
    for seed in seeds[:observed_count]:
        cumulative.update(
            str(row.get("task_key") or "")
            for row in records
            if int(row.get("seed", -1)) == seed
            and row.get("category") == "task_success"
        )
        curve.append(len(cumulative))
    return curve


def load_campaign_payload(campaign_root: Path | str) -> dict[str, Any]:
    """Normalize a campaign directory into the Web/CLI result schema."""
    root = Path(campaign_root).expanduser().resolve()
    manifest, state, result = _campaign_files(root)
    seeds = [int(seed) for seed in manifest.get("seeds") or ()]
    completed_seeds = [
        int(seed)
        for seed in result.get("completed_seeds", state.get("completed_seeds", []))
    ]
    records = [
        row for row in state.get("records", []) if isinstance(row, dict)
    ]
    journal_records = [_flat_record(row) for row in _journal_records(root)]
    metric_records = journal_records or records
    solved_keys = result.get("solved_task_keys", state.get("solved_tasks", []))
    solved = {str(task) for task in solved_keys or ()}
    solved.update(
        str(row.get("task_key") or "")
        for row in journal_records
        if row.get("category") == "task_success"
    )
    solved_count = int(result.get("solved_tasks", len(solved)))
    total_tasks = int(manifest.get("task_count") or len(SHORT_TASK_CATALOG))
    verdict_counts = Counter(
        str(row.get("category") or "") for row in metric_records
    )
    verdicts = dict(result.get("verdicts") or {})
    if not verdicts:
        verdicts = {
            "task_success": verdict_counts["task_success"],
            "task_failure": verdict_counts["task_failure"],
            "implementation_failure": verdict_counts["implementation_failure"],
            "infrastructure_excluded": sum(
                verdict_counts[category] for category in INFRASTRUCTURE
            ),
        }
    efficiency = dict(result.get("efficiency") or {})
    if not efficiency and journal_records:
        metered = [
            row
            for row in journal_records
            if str(row.get("category") or "") not in INFRASTRUCTURE
        ]
        token_values = [int(row.get("total_tokens", 0) or 0) for row in metered]
        efficiency = {
            "total_tokens": sum(token_values),
            "median_total_tokens": (
                float(statistics.median(token_values)) if token_values else 0.0
            ),
            "total_vlm_calls": sum(
                int(row.get("vlm_calls", 0) or 0) for row in metered
            ),
            "total_episode_elapsed_s": sum(
                float(row.get("elapsed_s", 0.0) or 0.0) for row in metered
            ),
        }
    pass_curve = [
        int(value) for value in result.get("pass_curve") or ()
    ] or _state_pass_curve(metric_records, seeds, completed_seeds)
    by_suite = dict(result.get("by_suite") or _suite_summary(solved))
    release_history = list(
        result.get("release_history", state.get("release_history", [])) or []
    )
    return {
        "schema": str(
            result.get("schema")
            or "roborsi.libero_short_campaign_dashboard.v1"
        ),
        "source_kind": "campaign",
        "source_name": root.name,
        "run_id": str(manifest.get("run_id") or root.name),
        "created_at": str(manifest.get("created_at") or ""),
        "mode": str(manifest.get("mode") or "unknown"),
        "status": str(result.get("status") or state.get("status") or "created"),
        "metric": str(manifest.get("metric") or result.get("metric") or ""),
        "claim_scope": str(
            manifest.get("claim_scope") or result.get("claim_scope") or ""
        ),
        "k": len(seeds),
        "completed_passes": len(completed_seeds),
        "completed_seeds": completed_seeds,
        "solved_tasks": solved_count,
        "total_tasks": total_tasks,
        "rate": solved_count / total_tasks if total_tasks else 0.0,
        "pass_curve": pass_curve,
        "by_suite": by_suite,
        "release_history": release_history,
        "current_release_id": str(state.get("current_release_id") or ""),
        "verdicts": verdicts,
        "task_success_records": int(verdicts.get("task_success", 0) or 0),
        "task_failure_records": int(verdicts.get("task_failure", 0) or 0),
        "implementation_failures": int(
            verdicts.get("implementation_failure", 0) or 0
        ),
        "infrastructure_excluded": int(
            verdicts.get("infrastructure_excluded", 0) or 0
        ),
        "total_tokens": int(efficiency.get("total_tokens", 0) or 0),
        "median_total_tokens": float(
            efficiency.get("median_total_tokens", 0.0) or 0.0
        ),
        "total_vlm_calls": int(efficiency.get("total_vlm_calls", 0) or 0),
        "total_elapsed_s": float(
            efficiency.get("total_episode_elapsed_s", 0.0) or 0.0
        ),
        "success_source": str(
            result.get("success_source")
            or manifest.get("success_source")
            or "final simulator predicate only"
        ),
        "protocol": {
            "model": str(manifest.get("model") or ""),
            "reasoning_effort": str(manifest.get("reasoning_effort") or ""),
            "controller": str(manifest.get("controller") or ""),
            "image_size": int(manifest.get("image_size") or 0),
            "horizon": int(manifest.get("horizon") or 0),
            "seeds": seeds,
        },
    }
